"""Detached MCP refresh refuses to reuse headers without runtime authority."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.database import Base
from xagent.web.models.mcp import MCPServer
from xagent.web.tools.config import WebToolConfig

_BINDINGS = [
    {
        "source": {"input_type": "secrets", "key": "authorization"},
        "target": {"target_type": "transport_headers", "key": "Authorization"},
    }
]


def _server():
    return MCPServer(
        id=17,
        name="local-remote",
        managed="external",
        transport="streamable_http",
        url="http://127.0.0.1:8765/mcp",
        headers={"Authorization": "Bearer stale-static"},
    )


def test_delegated_refresh_without_task_identity_never_uses_static_authorization():
    cfg = WebToolConfig(
        db=None,
        db_factory=lambda: (_ for _ in ()).throw(AssertionError("unexpected DB read")),
        request=None,
        user_id=7,
    )
    try:
        refresh = cfg._build_delegated_mcp_refresh_callback(
            server=_server(),
            runtime_bindings=_BINDINGS,
            allow_delegated_authorization=True,
        )
        assert refresh() is None
    finally:
        cfg.close()


def test_delegated_refresh_without_a_persisted_task_cannot_reuse_old_bearer(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-task.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    cfg = WebToolConfig(
        db=None,
        db_factory=factory,
        request=None,
        user_id=7,
        task_id="web_task_999",
    )
    try:
        refresh = cfg._build_delegated_mcp_refresh_callback(
            server=_server(),
            runtime_bindings=_BINDINGS,
            allow_delegated_authorization=True,
        )
        assert refresh() is None
    finally:
        cfg.close()
        engine.dispose()
