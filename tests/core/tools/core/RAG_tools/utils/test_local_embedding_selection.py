"""Selected LAN embedding models cannot silently resolve as another category."""

import pytest

from xagent.core.model.model import EmbeddingModelConfig, RerankModelConfig
from xagent.core.tools.core.RAG_tools.core.exceptions import EmbeddingAdapterError
from xagent.core.tools.core.RAG_tools.utils import model_resolver


class _LocalHub:
    def __init__(self, model):
        self.model = model
        self.closed = False

    def load(self, model_id):
        assert model_id == "selected-embedding"
        return self.model

    def close(self):
        self.closed = True


def test_selected_embedding_cannot_fall_through_to_environment_rerank_row(monkeypatch):
    hub = _LocalHub(
        RerankModelConfig(
            id="selected-embedding",
            model_name="local-reranker",
            model_provider="openai-compatible",
            base_url="http://rerank.internal/v1",
            abilities=["rerank"],
        )
    )
    monkeypatch.setattr(model_resolver, "_get_or_init_model_hub", lambda: hub)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "environment-embedding")
    monkeypatch.setenv("OPENAI_EMBEDDING_BASE_URL", "http://embed.internal/v1")

    with pytest.raises(
        EmbeddingAdapterError, match="exists but is not a EmbeddingModelConfig"
    ):
        model_resolver.resolve_embedding_adapter(model_id="selected-embedding")
    assert hub.closed


def test_selected_embedding_without_endpoint_fails_instead_of_using_env(monkeypatch):
    hub = _LocalHub(
        EmbeddingModelConfig(
            id="selected-embedding",
            model_name="internal-embedding",
            model_provider="openai-compatible",
            abilities=["embedding"],
        )
    )
    monkeypatch.setattr(model_resolver, "_get_or_init_model_hub", lambda: hub)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "environment-embedding")
    monkeypatch.setenv("OPENAI_EMBEDDING_BASE_URL", "http://embed.internal/v1")

    with pytest.raises(
        EmbeddingAdapterError, match="Failed to create adapter"
    ) as error:
        model_resolver.resolve_embedding_adapter(model_id="selected-embedding")
    assert "base_url" in error.value.details["error"]
    assert hub.closed
