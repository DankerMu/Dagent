from __future__ import annotations

from typing import Dict

import pytest

from xagent.core.model.model import EmbeddingModelConfig, RerankModelConfig
from xagent.core.model.storage.error import ModelNotFoundError
from xagent.core.tools.core.RAG_tools.core.schemas import SearchType
from xagent.core.tools.core.RAG_tools.utils import model_resolver
from xagent.core.tools.core.RAG_tools.utils.config_utils import coerce_search_config


class _StubHub:
    def __init__(self, models: Dict[str, object]) -> None:
        self._models = models

    def list(self) -> Dict[str, object]:
        return self._models

    def load(self, model_id: str) -> object:
        if model_id not in self._models:
            raise ModelNotFoundError(model_id)
        return self._models[model_id]


def test_resolve_embedding_hub_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that hub is prioritized when no explicit model_id is provided."""
    # Use "default" model ID to satisfy new strict resolution logic for placeholders
    stub_hub = _StubHub(
        {
            "default": EmbeddingModelConfig(
                id="default",
                model_name="hub-model",
                model_provider="openai-compatible",
                api_key="hub-key",
                base_url="http://model.internal/v1",
                abilities=["embedding"],
            )
        }
    )
    monkeypatch.setattr(model_resolver, "_get_or_init_model_hub", lambda: stub_hub)

    # Set env vars (should be ignored when hub is available)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_BASE_URL", "http://model.internal/v1")

    cfg, _ = model_resolver.resolve_embedding_adapter(model_id=None)
    # Hub should be used (priority), not env
    assert cfg.id == "default"


def test_resolve_embedding_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that env is used as fallback when hub fails."""
    # Mock recoverable hub unavailability.
    monkeypatch.setattr(model_resolver, "_get_or_init_model_hub", lambda: None)

    # Set env vars for fallback
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_BASE_URL", "http://model.internal/v1")
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSION", "2048")

    cfg, _ = model_resolver.resolve_embedding_adapter(model_id=None)
    assert cfg.id == "env-model"
    assert cfg.dimension == 2048


def test_coerce_search_config_prepares_for_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that coerce_search_config sets placeholder for resolver."""
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_BASE_URL", "http://model.internal/v1")
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSION", "1536")

    cfg = coerce_search_config({"top_k": 5})
    # coerce sets it to "none" (or "default") for resolver to handle later
    # It does NOT resolve env vars immediately
    assert cfg.embedding_model_id == "none"
    assert cfg.search_type == SearchType.HYBRID


def test_resolve_rerank_hub_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that hub is prioritized when no explicit model_id is provided."""
    # Use "default" model ID
    stub_hub = _StubHub(
        {
            "default": RerankModelConfig(
                id="default",
                model_name="hub-rerank",
                model_provider="openai-compatible",
                api_key="hub-key",
                base_url="http://model.internal/v1",
                abilities=["rerank"],
            )
        }
    )
    monkeypatch.setattr(model_resolver, "_get_or_init_model_hub", lambda: stub_hub)

    # Set env vars (should be ignored when hub is available)
    monkeypatch.setenv("OPENAI_RERANK_MODEL", "env-rerank")
    monkeypatch.setenv("OPENAI_RERANK_BASE_URL", "http://model.internal/v1")

    cfg, _ = model_resolver.resolve_rerank_adapter(model_id=None)
    # Hub should be used (priority), not env
    assert cfg.id == "default"


def test_resolve_rerank_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that env is used as fallback when hub fails."""
    # Mock recoverable hub unavailability.
    monkeypatch.setattr(model_resolver, "_get_or_init_model_hub", lambda: None)

    # Set env vars for fallback
    monkeypatch.setenv("OPENAI_RERANK_MODEL", "env-rerank")
    monkeypatch.setenv("OPENAI_RERANK_BASE_URL", "http://model.internal/v1")
    monkeypatch.setenv("OPENAI_RERANK_TIMEOUT", "12")

    cfg, _ = model_resolver.resolve_rerank_adapter(model_id=None)
    assert cfg.id == "env-rerank"
