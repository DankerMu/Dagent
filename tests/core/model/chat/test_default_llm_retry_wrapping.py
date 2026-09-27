"""Env and default fallback paths must carry the shared retry layer too.

``create_base_llm`` installs ``RetryWrapper``, but three supported paths build
a provider client directly and never reach it. Their only retry cover used to
be the SDK's own budget, which #2605 sets to zero -- so without a wrapper a
single transient fault fails the task on its first response.
"""

from unittest.mock import AsyncMock

import httpx
import openai
import pytest

REQUEST = httpx.Request("POST", "http://model.internal/v1/chat/completions")


@pytest.fixture
def openai_env(monkeypatch):
    """An env-only deployment with an explicit LAN inference endpoint."""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.internal/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "local-chat")


def _budget_of(llm):
    wrapper = getattr(llm, "_retry_wrapper", None)
    return None if wrapper is None else wrapper.budget


class TestDefaultLlmCarriesTheRetryLayer:
    def test_agent_service_manager_default_is_wrapped(self, openai_env):
        """``AgentServiceManager.__init__`` builds this unconditionally."""
        from xagent.web.services.agent_service_manager import create_default_llm

        llm = create_default_llm()

        assert llm is not None
        budget = _budget_of(llm)
        assert budget is not None
        assert budget.deadline_seconds is not None

    def test_env_fallback_llm_is_wrapped(self, openai_env):
        """Reached from ``get_configured_defaults``' final fallback."""
        from xagent.web.services.llm_utils import create_llm_from_env

        llm = create_llm_from_env()

        assert llm is not None
        assert _budget_of(llm) is not None

    def test_default_vision_model_is_wrapped(self, openai_env, monkeypatch):
        monkeypatch.setenv("OPENAI_VISION_MODEL_NAME", "local-vision")
        from xagent.core.tools.adapters.vibe.vision_tool import (
            get_default_vision_model,
        )

        llm = get_default_vision_model()

        assert llm is not None
        assert _budget_of(llm) is not None

    async def test_a_transient_fault_is_retried_on_the_default_llm(
        self, openai_env, monkeypatch, mock_chat_completion
    ):
        """A transient provider failure recovers through the real fallback path."""
        from xagent.web.services.agent_service_manager import create_default_llm

        client = AsyncMock()
        client.chat.completions.create.side_effect = [
            openai.APIConnectionError(request=REQUEST),
            mock_chat_completion,
        ]
        monkeypatch.setattr(
            "xagent.core.model.chat.basic.openai.AsyncOpenAI",
            lambda **kwargs: client,
        )

        llm = create_default_llm()
        assert llm is not None

        result = await llm.chat([{"role": "user", "content": "hi"}])

        assert result["content"] == "Hello World"
        assert client.chat.completions.create.await_count == 2
