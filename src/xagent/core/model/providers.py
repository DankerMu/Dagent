import os
from typing import Any, Optional

_PROVIDER_ALIASES: dict[str, str] = {
    "openai_embedding": "openai",
}


# Provider-scoped base URL environment overrides. Keep these narrow so shared
# client implementations do not redirect sibling providers to the wrong API.
_BASE_URL_ENV_BY_PROVIDER: dict[str, str] = {
    "openai": "OPENAI_BASE_URL",
    "openai-compatible": "OPENAI_BASE_URL",
    "xinference": "XINFERENCE_BASE_URL",
}

_SUPPORTED_PROVIDER_METADATA: tuple[dict[str, Any], ...] = (
    {
        "id": "openai",
        "name": "OpenAI Compatible",
        "description": "OpenAI-compatible inference; an explicit base URL is required",
        "requires_base_url": True,
        "compatibility": "openai_compatible",
        "category": ["llm", "embedding", "image", "rerank"],
    },
    {
        "id": "openai-compatible",
        "name": "OpenAI-Compatible",
        "description": "OpenAI-compatible models; an explicit base URL is required",
        "requires_base_url": True,
        "compatibility": "openai_compatible",
        "category": ["llm", "embedding", "image", "rerank"],
    },
    {
        "id": "xinference",
        "name": "Xinference",
        "description": "Xinference models for local inference",
        "requires_base_url": True,
        "category": ["llm", "embedding", "image", "video", "speech", "rerank"],
    },
)


def _normalize_provider(provider: str) -> str:
    return provider.lower().strip()


def canonical_provider_name(provider: str) -> str:
    normalized = _normalize_provider(provider)
    return _PROVIDER_ALIASES.get(normalized, normalized)


def is_placeholder_api_key(api_key: Optional[str]) -> bool:
    if api_key is None:
        return True

    normalized = api_key.strip().strip("\"'")
    if not normalized:
        return True

    return normalized.startswith("your-") and normalized.endswith("-key")


def resolve_base_url_for_provider(
    provider: str, explicit_base_url: Optional[str] = None
) -> Optional[str]:
    """Resolve a provider base URL using explicit value, then scoped env.

    Environment overrides are keyed by canonical provider, not by client class.
    Cloud vendor defaults are never implied.
    """
    if explicit_base_url:
        return explicit_base_url

    canonical = canonical_provider_name(provider)
    env_name = _BASE_URL_ENV_BY_PROVIDER.get(canonical)
    if env_name and (env_value := os.getenv(env_name)):
        return env_value

    return None


def require_explicit_base_url(
    provider: str, explicit_base_url: Optional[str] = None
) -> str:
    """Resolve a configured endpoint or raise; never invent a public default."""
    resolved = resolve_base_url_for_provider(provider, explicit_base_url)
    if not resolved or not str(resolved).strip():
        raise ValueError(
            f"base_url is required for provider {canonical_provider_name(provider)!r}"
        )
    return str(resolved).strip()


def provider_compatibility_for_provider(provider: str) -> Optional[str]:
    provider_id = canonical_provider_name(provider)
    for provider_info in _SUPPORTED_PROVIDER_METADATA:
        if provider_info["id"] == provider_id:
            compatibility = provider_info.get("compatibility")
            return str(compatibility) if compatibility is not None else None
    return None


def provider_requires_base_url(provider: str) -> bool:
    """True when the provider's metadata marks base_url as mandatory.

    Unregistered providers (including provider+category combos like
    "xinference-rerank") default to False rather than raising, since
    callers already validate the provider exists via PROVIDER_FETCHERS.
    """
    provider_id = canonical_provider_name(provider)
    for provider_info in _SUPPORTED_PROVIDER_METADATA:
        if provider_info["id"] == provider_id:
            return bool(provider_info.get("requires_base_url", False))
    return False


def get_supported_provider_metadata() -> list[dict[str, Any]]:
    return [dict(provider) for provider in _SUPPORTED_PROVIDER_METADATA]
