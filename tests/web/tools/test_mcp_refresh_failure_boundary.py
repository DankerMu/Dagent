"""Failed refresh remains unavailable through the public agent-tool boundary."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.web.tools import test_mcp_oauth_runtime as fixtures
from tests.web.tools.test_mcp_oauth_runtime import (
    _add_grant,
    _add_mcp_oauth_server,
    _assert_unavailable_runtime_config,
    _load_configs,
)
from xagent.core.tools.adapters.vibe import mcp_tools
from xagent.core.utils.encryption import decrypt_value
from xagent.web.services import mcp_oauth as mcp_oauth_service

db_session = fixtures.db_session


@pytest.mark.asyncio
async def test_mcp_oauth_runtime_refresh_failure_retains_unavailable_without_static_fallback(
    db_session,
    monkeypatch,
):
    db, user, _ = db_session
    server = _add_mcp_oauth_server(db, user)
    grant = _add_grant(
        db,
        server=server,
        user=user,
        resource_owner_key=f"xagent:user:{user.id}",
        access_token="expired-access-token",
        refresh_token="refresh-token-123",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.commit()

    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "refresh token is invalid",
                "access_token": "leaked-access-token",
                "refresh_token": "leaked-refresh-token",
            },
        )

    def async_client_factory(*args, **kwargs):
        return real_async_client(transport=httpx.MockTransport(handler))

    async def skip_url_policy(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp_oauth_service, "validate_oauth_http_url", skip_url_policy)
    monkeypatch.setattr(mcp_oauth_service.httpx, "AsyncClient", async_client_factory)

    summary_tracer = AsyncMock()
    configs, cfg = await _load_configs(db, user, mcp_load_summary_tracer=summary_tracer)

    diagnostics = cfg.get_mcp_oauth_diagnostics()
    assert diagnostics[0]["code"] == "token_refresh_failed"
    assert diagnostics[0]["message"] == "refresh token is invalid"
    assert "leaked-access-token" not in str(diagnostics[0])
    assert "leaked-refresh-token" not in str(diagnostics[0])
    assert len(configs) == 1
    _assert_unavailable_runtime_config(configs[0], server, diagnostics[0])
    assert "leaked-access-token" not in str(configs[0])
    assert "leaked-refresh-token" not in str(configs[0])
    tools = await mcp_tools.create_mcp_tools(cfg)
    assert len(tools) == 1
    result = await tools[0].run_json_async({})
    assert result["reason"] == "token_refresh_failed"
    assert result["failure_code"] == "oauth_token_required"
    assert "leaked-access-token" not in str(result)
    assert "leaked-refresh-token" not in str(result)
    summary_tracer.trace_event.assert_awaited_once()
    assert summary_tracer.trace_event.await_args.kwargs["data"]["failures"] == [
        {"server_name": server.name, "reason": "token_refresh_failed"}
    ]
    db.refresh(grant)
    assert decrypt_value(grant.access_token) == "expired-access-token"
