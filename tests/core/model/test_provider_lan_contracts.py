"""LAN inference configuration must not inherit another provider's endpoint."""

import pytest

from xagent.core.model.providers import (
    get_supported_provider_metadata,
    is_placeholder_api_key,
    provider_requires_base_url,
    require_explicit_base_url,
    resolve_base_url_for_provider,
)


def test_provider_endpoints_are_scoped_and_explicit_url_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.lan/v1")
    monkeypatch.setenv("XINFERENCE_BASE_URL", "http://xinference.lan/v1")

    assert require_explicit_base_url(" OPENAI_EMBEDDING ") == "http://openai.lan/v1"
    assert require_explicit_base_url("xinference") == "http://xinference.lan/v1"
    assert require_explicit_base_url("xinference", " http://another.lan/v1 ") == (
        "http://another.lan/v1"
    )
    assert resolve_base_url_for_provider("unregistered") is None


def test_missing_or_whitespace_endpoint_never_falls_back_to_public_vendor(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("XINFERENCE_BASE_URL", raising=False)

    with pytest.raises(ValueError, match="base_url is required for provider 'openai'"):
        require_explicit_base_url("openai_embedding", "   ")
    with pytest.raises(
        ValueError, match="base_url is required for provider 'xinference'"
    ):
        require_explicit_base_url("xinference")


def test_supported_provider_metadata_requires_lan_endpoint():
    providers = get_supported_provider_metadata()
    assert {entry["id"] for entry in providers} == {
        "openai",
        "openai-compatible",
        "xinference",
    }
    assert all(entry["requires_base_url"] for entry in providers)
    assert provider_requires_base_url("xinference") is True
    assert provider_requires_base_url("unknown") is False
    assert provider_requires_base_url("xinference-rerank") is False


def test_placeholder_credentials_are_not_sent_as_real_keys():
    assert is_placeholder_api_key(None)
    assert is_placeholder_api_key(' "your-api-key" ')
    assert is_placeholder_api_key("  ")
    assert not is_placeholder_api_key("local-token")
