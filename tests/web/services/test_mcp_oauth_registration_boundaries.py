"""Generic MCP OAuth metadata and registration remain bounded and secret-safe."""

import gzip

import httpx
import pytest

from xagent.web.services import mcp_oauth as mcp_oauth_service
from xagent.web.services.mcp_oauth import (
    MCPOAuthDiscoveryError,
    OAuthAuthorizationServerMetadata,
    oauth_get,
    register_mcp_oauth_public_client,
)


def _registration_metadata() -> OAuthAuthorizationServerMetadata:
    return OAuthAuthorizationServerMetadata(
        url="https://auth.example.com/.well-known/oauth-authorization-server",
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
        registration_endpoint="https://auth.example.com/register",
        client_id_metadata_document_supported=True,
        raw={},
    )


@pytest.mark.asyncio
async def test_oauth_get_rejects_redirect_loops_at_a_bounded_hop_count():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "/start"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MCPOAuthDiscoveryError) as exc:
            await oauth_get("https://auth.example.com/start", client=client)

    assert exc.value.code == "metadata_not_found"
    assert "maximum allowed hops" in exc.value.message
    assert requests == ["https://auth.example.com/start"] * 6


@pytest.mark.asyncio
async def test_dynamic_registration_refuses_oversized_response_without_parsing_secrets(
    monkeypatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, content=b'{"client_id":"private-token"}' * 3000)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        mcp_oauth_service, "create_mcp_oauth_http_client", lambda **kwargs: client
    )

    with pytest.raises(MCPOAuthDiscoveryError) as exc:
        await register_mcp_oauth_public_client(
            _registration_metadata(),
            redirect_uri="https://api.xagent.test/api/mcp/oauth/callback",
        )

    assert exc.value.code == "client_registration_failed"
    assert "exceeded the allowed size" in exc.value.message
    assert "private-token" not in str(exc.value)


@pytest.mark.asyncio
async def test_dynamic_registration_refuses_compressed_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            content=gzip.compress(b'{"client_id":"private-token"}'),
            headers={"Content-Encoding": "gzip"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        mcp_oauth_service, "create_mcp_oauth_http_client", lambda **kwargs: client
    )

    with pytest.raises(MCPOAuthDiscoveryError) as exc:
        await register_mcp_oauth_public_client(
            _registration_metadata(),
            redirect_uri="https://api.xagent.test/api/mcp/oauth/callback",
        )

    assert exc.value.code == "client_registration_failed"
    assert "unsupported content encoding" in exc.value.message
    assert "private-token" not in str(exc.value)


@pytest.mark.asyncio
async def test_dynamic_registration_refuses_malformed_json_without_exposing_body(
    monkeypatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, content=b'{"client_id":"private-token"')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        mcp_oauth_service, "create_mcp_oauth_http_client", lambda **kwargs: client
    )

    with pytest.raises(MCPOAuthDiscoveryError) as exc:
        await register_mcp_oauth_public_client(
            _registration_metadata(),
            redirect_uri="https://api.xagent.test/api/mcp/oauth/callback",
        )

    assert exc.value.code == "client_registration_failed"
    assert exc.value.message == "OAuth response was not valid JSON"
    assert "private-token" not in str(exc.value)
