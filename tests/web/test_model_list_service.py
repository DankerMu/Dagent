"""Public model-listing service only reaches configured inference endpoints."""

from __future__ import annotations

import pytest

from xagent.core.model.providers import get_supported_provider_metadata
from xagent.web.services.model_list_service import fetch_models_from_provider


async def test_listing_requires_endpoint_even_when_api_key_is_set() -> None:
    with pytest.raises(ValueError, match="base_url is required"):
        await fetch_models_from_provider("openai-compatible", "key")


async def test_saved_cloud_provider_cannot_be_listed() -> None:
    with pytest.raises(ValueError, match="Unsupported model provider: dashscope"):
        await fetch_models_from_provider("dashscope", "key", "http://model.internal/v1")


def test_provider_catalog_contains_only_configured_lan_variants() -> None:
    providers = get_supported_provider_metadata()
    assert {item["id"] for item in providers} == {
        "openai",
        "openai-compatible",
        "xinference",
    }
    assert all(item["requires_base_url"] for item in providers)
    assert all("default_base_url" not in item for item in providers)
