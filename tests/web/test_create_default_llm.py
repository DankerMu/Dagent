"""The default LLM never infers a public provider endpoint."""

from xagent.core.model.chat.basic.openai import OpenAILLM
from xagent.web.services.agent_service_manager import create_default_llm


def test_unauthenticated_lan_endpoint_creates_default_llm(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.internal/v1")
    monkeypatch.setenv("OPENAI_MODEL", "lan-chat")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    llm = create_default_llm()
    assert llm is not None
    inner = getattr(llm, "_inner", llm)
    assert isinstance(inner, OpenAILLM)
    assert inner.base_url == "http://model.internal/v1"
    assert inner.model_name == "lan-chat"


def test_cloud_keys_cannot_bootstrap_a_default_without_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "lan-chat")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")

    assert create_default_llm() is None


def test_explicit_endpoint_without_model_does_not_invent_a_model(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.internal/v1")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert create_default_llm() is None
