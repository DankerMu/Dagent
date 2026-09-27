"""Local model setup never treats example credentials as real provider secrets."""

from xagent.web.services.agent_service_manager import create_default_llm


def test_placeholder_key_prevents_default_model_from_contacting_local_provider(
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("OPENAI_MODEL", "local-model")
    monkeypatch.setenv("OPENAI_API_KEY", "your-api-key")

    assert create_default_llm() is None


def test_invalid_local_model_endpoint_cannot_construct_a_default_model(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "   ")
    monkeypatch.setenv("OPENAI_MODEL", "local-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert create_default_llm() is None
