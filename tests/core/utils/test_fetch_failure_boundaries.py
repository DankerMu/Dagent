"""Malformed destinations and incomplete responses fail before consumption."""

import socket

import httpx
import pytest

from xagent.core.utils.security import fetch_public_http_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@documents.example/a",  # pragma: allowlist secret - rejected synthetic URL
        "https://documents.example:invalid/a",
    ],
)
async def test_invalid_destination_never_reaches_transport(monkeypatch, url):
    monkeypatch.delenv("XAGENT_HTTP_PRIVATE_NETWORKS", raising=False)

    def unexpected_request(request):
        pytest.fail("Invalid destination reached HTTP transport")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as client:
        with pytest.raises(ValueError):
            await fetch_public_http_bytes(
                client, url, max_content_bytes=1024, timeout=1
            )


@pytest.mark.asyncio
async def test_empty_dns_result_cannot_reach_transport(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [])

    def unexpected_request(request):
        pytest.fail("Unresolved destination reached HTTP transport")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected_request)
    ) as client:
        with pytest.raises(ValueError, match="did not resolve"):
            await fetch_public_http_bytes(
                client, "https://documents.example/a", max_content_bytes=1024, timeout=1
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (302, {}, "no Location"),
        (302, {"location": "/cycle"}, "redirect limit"),
        (200, {}, "empty"),
    ],
)
async def test_incomplete_response_or_redirect_cycle_is_rejected(
    monkeypatch, status, headers, error
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers=headers)
        )
    ) as client:
        with pytest.raises(ValueError, match=error):
            await fetch_public_http_bytes(
                client,
                "https://documents.example/a",
                max_content_bytes=1024,
                timeout=1,
                require_non_empty=True,
            )
