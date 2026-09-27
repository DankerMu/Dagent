"""Service to fetch available models from configured inference endpoints."""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from ...core.model.providers import (
    canonical_provider_name,
    get_supported_provider_metadata,
)
from ...core.utils.security import redact_sensitive_text

logger = logging.getLogger(__name__)


async def fetch_openai_models(
    api_key: str, base_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetch models from a configured OpenAI-compatible endpoint."""
    from ...core.model.chat.basic.openai import OpenAILLM

    return await OpenAILLM.list_available_models(api_key, base_url)


async def fetch_xinference_models(
    api_key: str, base_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetch available models from a configured Xinference server."""
    if not base_url:
        raise ValueError("base_url is required for Xinference")

    from ...core.model.chat.basic.xinference import XinferenceLLM

    return await XinferenceLLM.list_available_models(base_url=base_url, api_key=api_key)


async def fetch_xinference_rerank_models(
    api_key: str, base_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetch available rerank models from a Xinference server."""
    if not base_url:
        raise ValueError("base_url is required for Xinference rerank")

    from ...core.model.rerank.xinference import XinferenceRerank

    return XinferenceRerank.list_available_models(base_url=base_url, api_key=api_key)


async def fetch_xinference_video_models(
    api_key: str, base_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetch available video models from a Xinference server."""
    if not base_url:
        raise ValueError("base_url is required for Xinference video")

    from ...core.model.chat.basic.xinference import _normalize_model_list_response

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    timeout = aiohttp.ClientTimeout(total=30.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{base_url.rstrip('/')}/v1/models", headers=headers
        ) as response:
            if response.status != 200:
                try:
                    detail = await response.json()
                except Exception:
                    detail = await response.text()
                raise RuntimeError(
                    f"Failed to list Xinference video models: HTTP {response.status}, detail: {detail}"
                )
            response_data = await response.json()
    data_list = response_data.get("data", []) if isinstance(response_data, dict) else []
    models_list = {
        str(item["id"]): item
        for item in data_list
        if isinstance(item, dict) and item.get("id")
    }
    normalized_models = _normalize_model_list_response(models_list)

    result: List[Dict[str, Any]] = []
    for model_uid, model_info in normalized_models:
        abilities = [str(item) for item in model_info.get("model_ability", [])]
        model_type = str(model_info.get("model_type", ""))
        is_video = model_type == "video" or any(
            "video" in ability.lower() for ability in abilities
        )
        if not is_video:
            continue
        result.append(
            {
                "id": model_info.get("model_name", model_uid),
                "model_uid": model_uid,
                "model_type": model_type,
                "category": "video",
                "model_ability": ["generate"],
                "abilities": ["generate"],
                "description": model_info.get("model_description", ""),
            }
        )
    return result


PROVIDER_FETCHERS: Dict[
    str, Callable[[str, Optional[str]], Awaitable[List[Dict[str, Any]]]]
] = {
    "openai": fetch_openai_models,
    "openai-compatible": fetch_openai_models,
    "xinference": fetch_xinference_models,
    "xinference-rerank": fetch_xinference_rerank_models,
    "xinference-video": fetch_xinference_video_models,
}


async def fetch_models_from_provider(
    provider: str,
    api_key: str,
    base_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch models from a supported provider with an explicit endpoint."""
    provider_id = canonical_provider_name(provider)
    fetcher = PROVIDER_FETCHERS.get(provider_id)
    if not fetcher:
        raise ValueError(f"Unsupported model provider: {provider}")
    if not base_url or not base_url.strip():
        raise ValueError(f"base_url is required for provider {provider!r}")

    try:
        return await fetcher(api_key, base_url.strip())
    except Exception as exc:
        logger.error(
            "Error fetching models from %s: %s",
            provider,
            redact_sensitive_text(str(exc)),
        )
        raise


def get_supported_providers() -> List[Dict[str, Any]]:
    """Get the configured inference provider types."""
    return get_supported_provider_metadata()
