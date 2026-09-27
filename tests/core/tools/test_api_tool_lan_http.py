"""Behavior of the public API tool against an isolated HTTP transport."""

import base64
import json
from functools import partial
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from xagent.core.tools.adapters.vibe.api_tool import APITool
from xagent.core.tools.core.api_tool import APIClientCore


@pytest.fixture
def lan_transport(monkeypatch):
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/redirect":
            return httpx.Response(302, headers={"location": "/result"})
        if path == "/result":
            return httpx.Response(200, json={"location": "lan"})
        if path == "/oversize":
            return httpx.Response(200, text="a" * 50)
        if path == "/latin":
            return httpx.Response(
                200,
                content="café".encode("iso-8859-1"),
                headers={"content-type": "text/plain; charset=iso-8859-1"},
            )
        if path == "/broken-json":
            return httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"}
            )
        if path == "/unavailable":
            raise httpx.ConnectError("LAN offline")
        if path == "/deny":
            return httpx.Response(403, json={"reason": "forbidden"})
        return httpx.Response(200, json={"method": request.method})

    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        partial(original_client, transport=httpx.MockTransport(respond)),
    )
    return requests


async def test_lan_http_query_credentials_override_explicit_query_and_preserve_existing_url(
    lan_transport, monkeypatch
):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    tool = APITool()

    result = await tool.run_json_async(
        {
            "url": "http://lan.invalid/echo?existing=keep",
            "method": "post",
            "params": {"existing": "replacement", "key": "old"},
            "auth_type": "API_KEY_QUERY",
            "api_key_param": "key",  # pragma: allowlist secret - parameter name, not a key
            "auth_token": "new",
            "body": {"name": "Ada"},
        }
    )

    assert result["status_code"] == 200
    assert result["body"] == {"method": "POST"}
    assert parse_qs(urlparse(str(lan_transport[0].url)).query) == {
        "existing": ["replacement"],
        "key": ["new"],
    }
    assert json.loads(lan_transport[0].content) == {"name": "Ada"}
    assert lan_transport[0].headers["content-type"] == "application/json"


async def test_lan_http_basic_auth_and_form_encoding_respect_caller_headers(
    lan_transport,
):
    client = APIClientCore()
    result = await client.call_api(
        "http://lan.invalid/echo",
        method="POST",
        auth_type="basic",
        auth_token="ada:pass",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body={"name": "Ada Lovelace", "tag": "a&b"},
    )

    assert result["success"] is True
    assert lan_transport[0].headers["authorization"] == (
        "Basic " + base64.b64encode(b"ada:pass").decode()
    )
    assert lan_transport[0].content == b"name=Ada+Lovelace&tag=a%26b"
    assert (
        lan_transport[0].headers["content-type"] == "application/x-www-form-urlencoded"
    )


async def test_lan_http_explicit_key_header_is_not_overwritten(lan_transport):
    client = APIClientCore()
    result = await client.call_api(
        "http://lan.invalid/echo",
        auth_type="api_key",
        auth_token="ignored",
        headers={"x-api-key": "chosen"},
    )

    assert result["success"] is True
    assert lan_transport[0].headers["x-api-key"] == "chosen"


async def test_lan_http_response_limit_drops_payload_without_buffering_it(
    lan_transport,
):
    result = await APIClientCore(max_response_size=10).call_api(
        "http://lan.invalid/oversize"
    )

    assert result["success"] is False
    assert result["status_code"] == 200
    assert result["body"] is None
    assert "Response too large" in result["error"]


async def test_lan_http_redirect_policy_and_non_success_body(lan_transport):
    client = APIClientCore()
    stopped = await client.call_api(
        "http://lan.invalid/redirect", allow_redirects=False
    )
    followed = await client.call_api(
        "http://lan.invalid/redirect", allow_redirects=True
    )
    denied = await client.call_api("http://lan.invalid/deny")

    assert stopped["status_code"] == 302
    assert followed["body"] == {"location": "lan"}
    assert denied["success"] is False
    assert denied["status_code"] == 403
    assert denied["body"] == {"reason": "forbidden"}
    assert denied["error"] == "HTTP 403"


async def test_lan_http_exhausted_retries_return_transport_failure(lan_transport):
    result = await APIClientCore(default_retry_count=1).call_api(
        "http://lan.invalid/unavailable"
    )

    assert result["success"] is False
    assert result["status_code"] == 0
    assert result["error"] == "Request failed after 2 attempts: LAN offline"
    assert len(lan_transport) == 2


async def test_invalid_lan_url_is_rejected_without_reaching_transport(lan_transport):
    result = await APIClientCore().call_api("file:///etc/passwd")

    assert result["success"] is False
    assert result["status_code"] == 0
    assert "Invalid URL" in result["error"]
    assert lan_transport == []


async def test_lan_http_decodes_text_and_recovers_invalid_json(lan_transport):
    client = APIClientCore()
    text = await client.call_api("http://lan.invalid/latin")
    malformed = await client.call_api("http://lan.invalid/broken-json")
    assert text["body"] == "café"
    assert malformed["body"] == "not json"


def test_lan_http_async_only_tool_rejects_synchronous_execution():
    with pytest.raises(NotImplementedError, match="only supports async execution"):
        APITool().run_json_sync({"url": "http://lan.invalid/echo"})


async def test_lan_http_tool_formats_text_and_error_for_agent_consumers(lan_transport):
    tool = APITool()
    success = await tool.run_json_async({"url": "http://lan.invalid/latin"})
    denied = await tool.run_json_async({"url": "http://lan.invalid/deny"})

    assert "café" in tool.return_value_as_string(success)
    assert "HTTP 403" in tool.return_value_as_string(denied)
