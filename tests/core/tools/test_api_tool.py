"""
Tests for API Tool
"""

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from xagent.core.tools.adapters.vibe.api_tool import APICallArgs, APITool
from xagent.core.tools.core.api_tool import APIClientCore, call_api


def _single_value_query_args(url: str) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(urlparse(url).query).items()}


@pytest.fixture
def mock_httpbin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_make_request(
        self: APIClientCore,
        *,
        url: str,
        method: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        data: str | bytes | None,
        timeout: int,
        proxy_url: str | None,
        allow_redirects: bool,
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        path = parsed.path
        body: dict[str, Any]
        status_code = 200

        if path == "/get":
            body = {"args": _single_value_query_args(url)}
        elif path == "/post":
            parsed_data = json.loads(
                data.decode() if isinstance(data, bytes) else data or "{}"
            )
            body = {"json": parsed_data}
        elif path == "/bearer":
            token = headers.get("Authorization", "").removeprefix("Bearer ")
            body = {"authenticated": bool(token), "token": token}
        elif path == "/headers":
            body = {"headers": headers}
        elif path.startswith("/status/"):
            status_code = int(path.removeprefix("/status/"))
            body = {}
        else:
            status_code = 404
            body = {}

        return {
            "success": 200 <= status_code < 300,
            "status_code": status_code,
            "headers": {"content-type": "application/json"},
            "body": body,
            "error": None if 200 <= status_code < 300 else f"HTTP {status_code}",
        }

    monkeypatch.setattr(APIClientCore, "_make_request", mock_make_request)


class TestAPIClientCore:
    """Test core API client functionality"""

    @pytest.mark.asyncio
    async def test_get_request(self, mock_httpbin: None):
        """Test basic GET request"""
        client = APIClientCore()
        result = await client.call_api(
            url="https://httpbin.org/get",
            method="GET",
            params={"test": "value"},
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert "body" in result
        assert result["body"]["args"]["test"] == "value"

    @pytest.mark.asyncio
    async def test_post_json_request(self, mock_httpbin: None):
        """Test POST request with JSON body"""
        client = APIClientCore()
        result = await client.call_api(
            url="https://httpbin.org/post",
            method="POST",
            body={"name": "test", "value": 123},
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["body"]["json"]["name"] == "test"
        assert result["body"]["json"]["value"] == 123

    @pytest.mark.asyncio
    async def test_bearer_auth(self, mock_httpbin: None):
        """Test Bearer token authentication"""
        client = APIClientCore()
        result = await client.call_api(
            url="https://httpbin.org/bearer",
            method="GET",
            auth_type="bearer",
            auth_token="test-token-123",
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["body"]["authenticated"] is True
        assert result["body"]["token"] == "test-token-123"

    @pytest.mark.asyncio
    async def test_api_key_query_auth(self, mock_httpbin: None):
        """Test API key in query parameters using convenience function"""
        result = await call_api(
            url="https://httpbin.org/get",
            method="GET",
            auth_type="api_key_query",
            auth_token="test-key-123",
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        # Verify the api_key was added to query params
        assert result["body"]["args"]["api_key"] == "test-key-123"

    @pytest.mark.asyncio
    async def test_api_key_query_custom_param(self, mock_httpbin: None):
        """Test API key in custom query parameter"""
        result = await call_api(
            url="https://httpbin.org/get",
            method="GET",
            auth_type="api_key_query",
            auth_token="test-key-123",
            api_key_param="token",
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        # Verify the custom param was added to query params
        assert result["body"]["args"]["token"] == "test-key-123"

    @pytest.mark.asyncio
    async def test_api_key_query_auth_type_is_case_insensitive(
        self, mock_httpbin: None
    ):
        """Regression: this check used to be an exact-case string compare,
        unlike _prepare_headers' auth_type.lower() handling of bearer/
        basic/api_key just below it and has_auth_credentials' own lower()
        call - auth_type="API_KEY_QUERY" (or any non-lowercase spelling)
        would silently attach no credential here while has_auth_credentials
        reported one was present, suppressing the connector hint for
        exactly the resulting unauthenticated 401/403."""
        result = await call_api(
            url="https://httpbin.org/get",
            method="GET",
            auth_type="API_KEY_QUERY",
            auth_token="test-key-123",
        )

        assert result["success"] is True
        assert result["body"]["args"]["api_key"] == "test-key-123"

    @pytest.mark.asyncio
    async def test_custom_headers(self, mock_httpbin: None):
        """Test custom headers"""
        client = APIClientCore()
        result = await client.call_api(
            url="https://httpbin.org/headers",
            method="GET",
            headers={"X-Custom-Header": "custom-value"},
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert "X-Custom-Header" in result["body"]["headers"]

    @pytest.mark.asyncio
    async def test_invalid_url(self):
        """Test invalid URL handling"""
        client = APIClientCore()
        result = await client.call_api(url="not-a-valid-url")

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_url_httpx_rejects_after_merging_params_does_not_raise(self):
        """Regression: _is_valid_url's cheap urlparse-based check (scheme +
        non-empty netloc) lets through a URL httpx.URL() itself later
        rejects (e.g. a non-numeric port) - only reachable once query
        params get merged into the URL, since a request with none never
        reaches that merge step. That merge used to run outside the retry
        loop's try/except, so it raised straight out of call_api instead of
        returning the documented dict shape."""
        client = APIClientCore()
        result = await client.call_api(
            url="http://example.com:abc/path", params={"q": "1"}
        )

        assert result["success"] is False
        assert result["status_code"] == 0
        assert isinstance(result["error"], str)

    @pytest.mark.asyncio
    async def test_retry_mechanism(self, monkeypatch: pytest.MonkeyPatch):
        """Network exceptions are retried and the successful third call is returned."""
        client = APIClientCore(default_retry_count=2)
        call_count = 0

        async def flaky_request(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("temporary failure")
            return {
                "success": True,
                "status_code": 200,
                "headers": {},
                "body": {"ok": True},
                "error": None,
            }

        monkeypatch.setattr(client, "_make_request", flaky_request)
        result = await client.call_api(url="https://example.com/data")

        assert call_count == 3
        assert result["success"] is True
        assert result["status_code"] == 200


class TestCredentialFreeLogging:
    """Regression: call_api's own request/completion logs, and the
    adapter's connector-hint log, used to interpolate the raw URL - which
    can carry a real credential (Basic-Auth userinfo a caller embedded, or
    an api_key_query auth_token merged into the query string before the
    request runs). For CustomApiTool in particular, that URL can hold a
    real decrypted, persisted secret (see api_tool_adapter.py's
    _replace_secrets/decrypt_value), so this isn't just a hypothetical -
    every one of these log lines needed the same redaction final_url
    already got."""

    @pytest.mark.asyncio
    async def test_startup_log_does_not_leak_basic_auth_userinfo(
        self, mock_httpbin: None, caplog: pytest.LogCaptureFixture
    ):
        caplog.set_level("INFO")
        await call_api(url="https://user:leaked-secret@httpbin.org/get")

        assert "leaked-secret" not in caplog.text

    @pytest.mark.asyncio
    async def test_completion_log_does_not_leak_api_key_query_credential(
        self, mock_httpbin: None, caplog: pytest.LogCaptureFixture
    ):
        caplog.set_level("INFO")
        await call_api(
            url="https://httpbin.org/get",
            auth_type="api_key_query",
            auth_token="leaked-secret",
        )

        assert "leaked-secret" not in caplog.text

    @pytest.mark.asyncio
    async def test_logs_redact_all_arbitrary_query_values(
        self, mock_httpbin: None, caplog: pytest.LogCaptureFixture
    ):
        """This call site accepts arbitrary params, so it cannot rely on the
        incomplete keyword inventory tracked by open issue #2356."""
        caplog.set_level("INFO")
        await call_api(
            url="https://httpbin.org/get?client_secret=url-secret",
            params={"subscription-key": "param-secret", "q": "ordinary-value"},
        )

        assert "url-secret" not in caplog.text
        assert "param-secret" not in caplog.text
        assert "ordinary-value" not in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_merged_url_error_does_not_leak_query_values(self):
        result = await call_api(
            url="http://example.com:abc/path?client_secret=url-secret",
            params={"q": "ordinary-value"},
        )

        assert "url-secret" not in result["error"]
        assert "ordinary-value" not in result["error"]


class TestAPITool:
    """Test APITool adapter"""

    def test_args_schema(self):
        """Test argument schema"""
        tool = APITool()
        args_type = tool.args_type()

        assert args_type == APICallArgs

        # Validate args
        args = APICallArgs.model_validate(
            {
                "url": "https://api.example.com",
                "method": "POST",
                "body": {"test": "value"},
            }
        )
        assert args.url == "https://api.example.com"
        assert args.method == "POST"
        assert args.body == {"test": "value"}

    @pytest.mark.asyncio
    async def test_api_call_execution(self, monkeypatch):
        """Test API tool wrapper delegates to client core"""
        tool = APITool()

        async def mock_call_api(**kwargs):
            assert kwargs["url"] == "https://httpbin.org/get"
            assert kwargs["method"] == "GET"
            assert kwargs["params"] == {"test": "value"}
            return {
                "success": True,
                "status_code": 200,
                "headers": {"content-type": "application/json"},
                "body": {"ok": True},
                "error": None,
            }

        monkeypatch.setattr(tool._client, "call_api", mock_call_api)

        result = await tool.run_json_async(
            {
                "url": "https://httpbin.org/get",
                "method": "GET",
                "params": {"test": "value"},
            }
        )

        assert result["success"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_api_call_with_auth(self, monkeypatch):
        """Test API call forwards authentication args"""
        tool = APITool()

        async def mock_call_api(**kwargs):
            assert kwargs["auth_type"] == "bearer"
            assert kwargs["auth_token"] == "test-token"
            return {
                "success": True,
                "status_code": 200,
                "headers": {},
                "body": {"authenticated": True},
                "error": None,
            }

        monkeypatch.setattr(tool._client, "call_api", mock_call_api)

        result = await tool.run_json_async(
            {
                "url": "https://httpbin.org/bearer",
                "method": "GET",
                "auth_type": "bearer",
                "auth_token": "test-token",
            }
        )

        assert result["success"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_api_call_with_post_body(self, monkeypatch):
        """Test API call forwards POST body and headers"""
        tool = APITool()

        async def mock_call_api(**kwargs):
            assert kwargs["method"] == "POST"
            assert kwargs["body"] == {"name": "test", "value": 123}
            assert kwargs["headers"] == {"Content-Type": "application/json"}
            return {
                "success": True,
                "status_code": 200,
                "headers": {},
                "body": {"name": "test", "value": 123},
                "error": None,
            }

        monkeypatch.setattr(tool._client, "call_api", mock_call_api)

        result = await tool.run_json_async(
            {
                "url": "https://httpbin.org/post",
                "method": "POST",
                "body": {"name": "test", "value": 123},
                "headers": {"Content-Type": "application/json"},
            }
        )

        assert result["success"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_api_call_with_api_key_query(self, monkeypatch):
        """Test API key query options are forwarded via tool"""
        tool = APITool()

        async def mock_call_api(**kwargs):
            assert kwargs["auth_type"] == "api_key_query"
            assert kwargs["auth_token"] == "my-secret-key-123"
            assert kwargs["api_key_param"] == "key"
            return {
                "success": True,
                "status_code": 200,
                "headers": {},
                "body": {"args": {"key": "my-secret-key-123"}},
                "error": None,
            }

        monkeypatch.setattr(tool._client, "call_api", mock_call_api)

        result = await tool.run_json_async(
            {
                "url": "https://httpbin.org/get",
                "method": "GET",
                "auth_type": "api_key_query",
                "auth_token": "my-secret-key-123",
                "api_key_param": "key",
            }
        )

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["body"]["args"]["key"] == "my-secret-key-123"

    @pytest.mark.asyncio
    async def test_malformed_url_does_not_crash(self):
        """Invalid IPv6 host syntax returns a structured failure."""
        tool = APITool()
        result = await tool.run_json_async({"url": "https://[::1"})

        assert result["success"] is False
        assert result["status_code"] == 0
        assert isinstance(result.get("error"), str)

    def test_return_value_formatting(self):
        """Test return value formatting"""
        tool = APITool()

        # Success case
        success_result = {
            "success": True,
            "status_code": 200,
            "body": {"result": "success"},
        }
        formatted = tool.return_value_as_string(success_result)
        assert "✅" in formatted
        assert "200" in formatted

        # Error case
        error_result = {
            "success": False,
            "error": "Connection failed",
        }
        formatted = tool.return_value_as_string(error_result)
        assert "❌" in formatted
        assert "Connection failed" in formatted


class TestConvenienceFunctions:
    """Test convenience functions"""

    @pytest.mark.asyncio
    async def test_call_api_function(self, mock_httpbin: None):
        """Test convenience call_api function"""
        result = await call_api(
            url="https://httpbin.org/get",
            method="GET",
            params={"test": "value"},
        )

        assert result["success"] is True
        assert result["status_code"] == 200
