"""MCP clients inherit system proxies only when no explicit proxy is configured."""

import requests

from xagent.web.tools.mcp import utils


def test_system_proxy_fallback_preserves_explicit_client_configuration(monkeypatch):
    # Register restoration even for initially absent keys: setup_proxy_env writes
    # to os.environ directly, so deleting an already-missing key is not enough.
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(
        utils.urllib.request,
        "getproxies",
        lambda: {
            "http": "http://system-proxy.invalid:8080",
            "https": "http://system-tls-proxy.invalid:8443",
        },
    )

    utils.setup_proxy_env()
    selected = requests.utils.get_environ_proxies("https://service.invalid")
    assert selected["http"] == "http://system-proxy.invalid:8080"
    assert selected["https"] == "http://system-tls-proxy.invalid:8443"

    monkeypatch.setenv("HTTPS_PROXY", "http://explicit-proxy.invalid:3128")
    utils.setup_proxy_env()
    selected = requests.utils.get_environ_proxies("https://service.invalid")
    assert selected["https"] == "http://explicit-proxy.invalid:3128"
