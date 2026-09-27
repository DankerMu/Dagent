"""Explicit LAN access must retain DNS pinning and redirect containment."""

import socket

import httpx
import pytest

from xagent.config import get_http_private_networks
from xagent.core.utils.security import (
    PrivateNetworkHostError,
    fetch_public_http_bytes,
    validate_public_http_url,
)


@pytest.fixture
def lan_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *_: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.20.8", port))
        ],
    )


@pytest.mark.asyncio
async def test_lan_fetch_requires_explicit_network_and_pins_connection(
    monkeypatch, lan_dns
):
    monkeypatch.delenv("XAGENT_HTTP_PRIVATE_NETWORKS", raising=False)
    with pytest.raises(PrivateNetworkHostError):
        await validate_public_http_url("http://knowledge.internal/manual")
    monkeypatch.setenv("XAGENT_HTTP_PRIVATE_NETWORKS", "192.168.20.0/24")

    def serve(request):
        assert request.url.host == "192.168.20.8"
        assert request.headers["host"] == "knowledge.internal"
        return httpx.Response(200, content=b"internal manual")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        response = await fetch_public_http_bytes(
            client,
            "http://knowledge.internal/manual",
            max_content_bytes=1024,
            timeout=1,
        )
    assert response.content == b"internal manual"


@pytest.mark.asyncio
async def test_allowed_lan_redirect_cannot_escape_to_metadata(monkeypatch, lan_dns):
    monkeypatch.setenv("XAGENT_HTTP_PRIVATE_NETWORKS", "192.168.20.0/24")
    visited = []

    def redirect(request):
        visited.append(request.url.host)
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(PrivateNetworkHostError):
            await fetch_public_http_bytes(
                client,
                "http://knowledge.internal/manual",
                max_content_bytes=1024,
                timeout=1,
            )
    assert visited == ["192.168.20.8"]


@pytest.mark.asyncio
async def test_dns_mixed_allowed_and_forbidden_results_fail_closed(monkeypatch):
    monkeypatch.setenv("XAGENT_HTTP_PRIVATE_NETWORKS", "192.168.20.0/24")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))
            for ip in ("192.168.20.8", "127.0.0.1")
        ],
    )
    with pytest.raises(PrivateNetworkHostError):
        await validate_public_http_url("http://knowledge.internal/manual")


@pytest.mark.parametrize(
    "value", ["0.0.0.0/0", "127.0.0.0/8", "169.254.0.0/16", "::/0", "not-a-network"]
)
def test_lan_allowlist_rejects_unsafe_or_malformed_ranges(monkeypatch, value):
    monkeypatch.setenv("XAGENT_HTTP_PRIVATE_NETWORKS", value)
    with pytest.raises(ValueError):
        get_http_private_networks()


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["fd00:ec2::254", "fd00:ec2::254%25eth0"])
async def test_ula_allowlist_does_not_admit_cloud_metadata(monkeypatch, host):
    monkeypatch.setenv("XAGENT_HTTP_PRIVATE_NETWORKS", "fc00::/7")
    with pytest.raises(PrivateNetworkHostError):
        await validate_public_http_url(f"http://[{host}]/latest/meta-data")
