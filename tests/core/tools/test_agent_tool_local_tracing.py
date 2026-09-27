"""Delegated agents use an explicitly configured local tracing endpoint."""

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tests.core.tools.test_create_agent_tool import _create_session
from tests.utils.mock_helpers import create_langfuse_mock
from xagent.core.tools.adapters.vibe.agent_tool import AgentTool
from xagent.core.tracing.langfuse.handler import LangfuseTraceHandler
from xagent.web.models.agent import Agent, AgentStatus
from xagent.web.models.model import Model
from xagent.web.models.user import User


@pytest.mark.asyncio
async def test_agent_tool_injects_langfuse_tracer(
    mocker, monkeypatch, langfuse_client_reset
) -> None:
    db, db_path, SessionLocal = _create_session()
    try:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret")
        monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.internal:3000")
        create_langfuse_mock(mocker)

        user = User(username="testuser9", password_hash="x", is_admin=False)
        db.add(user)
        db.commit()
        db.refresh(user)

        model = Model(
            model_id="test-model-id",
            category="llm",
            model_provider="openai",
            model_name="gpt-4",
            api_key="test-api-key",  # pragma: allowlist secret - mocked model fixture
            base_url="https://api.openai.com/v1",
            temperature=0.7,
            abilities=["chat"],
        )
        db.add(model)
        db.commit()
        db.refresh(model)

        agent = Agent(
            user_id=user.id,
            name="Delegated Agent",
            description="Nested agent",
            instructions="You are delegated.",
            status=AgentStatus.PUBLISHED,
            execution_mode="balanced",
            models={"general": model.id},
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

        tool = AgentTool(
            agent_id=agent.id,
            agent_name=agent.name,
            agent_description=agent.description or "",
            session_factory=SessionLocal,
            user_id=user.id,
            task_id="parent-task-1",
        )

        with (
            patch(
                "xagent.web.services.llm_utils.UserAwareModelStorage"
            ) as mock_storage_class,
            patch("xagent.core.agent.service.AgentService") as mock_agent_service_class,
            patch("xagent.core.memory.in_memory.InMemoryMemoryStore"),
        ):
            mock_storage = Mock()
            mock_llm = Mock()
            mock_storage.get_llm_by_name_with_access.return_value = mock_llm
            mock_storage_class.return_value = mock_storage

            mock_agent_service = mock_agent_service_class.return_value
            mock_agent_service.execute_task = AsyncMock(
                return_value={"output": "nested response"}
            )

            result = await tool.run_json_async({"task": "do nested work"})

        assert result["response"] == "nested response"
        assert mock_agent_service_class.call_args.kwargs["pattern"] == "react"
        tracer = mock_agent_service_class.call_args.kwargs["tracer"]
        assert any(
            isinstance(handler, LangfuseTraceHandler) for handler in tracer.handlers
        )
    finally:
        db.close()
        try:
            os.remove(db_path)
        except OSError:
            pass
