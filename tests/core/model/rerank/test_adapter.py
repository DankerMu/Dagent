"""Public rerank factory rejects retired providers and requires LAN endpoints."""

from unittest.mock import Mock, patch

import pytest

from xagent.core.model.model import RerankModelConfig
from xagent.core.model.rerank import OpenAICompatibleRerank, XinferenceRerank
from xagent.core.model.rerank.adapter import _create_rerank_model


def _config(
    provider: str, base_url: str | None = "http://model.internal/v1"
) -> RerankModelConfig:
    return RerankModelConfig(
        id=f"test-{provider}",
        model_name="lan-rerank",
        model_provider=provider,
        api_key=None,
        base_url=base_url,
    )


def test_xinference_rerank_remains_available():
    model = _create_rerank_model(_config("xinference"))
    assert isinstance(model, XinferenceRerank)


@pytest.mark.parametrize("provider", ["openai", "openai-compatible"])
def test_compatible_rerank_requires_explicit_endpoint(provider, monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="base_url is required"):
        _create_rerank_model(_config(provider, None))


@pytest.mark.parametrize("provider", ["dashscope", "elevenlabs"])
def test_saved_retired_rerank_provider_is_rejected(provider):
    with pytest.raises(ValueError, match=f"Unsupported rerank provider: {provider}"):
        _create_rerank_model(_config(provider))


def test_compatible_rerank_uses_lan_http_contract_without_api_key():
    response = Mock()
    response.json.return_value = {
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.2},
        ]
    }
    with patch("requests.post", return_value=response) as post:
        model = _create_rerank_model(_config("openai-compatible"))
        assert isinstance(model, OpenAICompatibleRerank)
        assert model.compress(["one", "two"], "query") == ["two", "one"]
    assert post.call_args.args == ("http://model.internal/v1/rerank",)
    assert "Authorization" not in post.call_args.kwargs["headers"]
