"""Classify persisted catalog server definitions without deleting stored rows."""

from typing import Any, cast

from sqlalchemy.orm import Session

from ...core.tools.adapters.vibe.selection_spec import normalize_mcp_server_name
from ..models.mcp import MCPServer, UserMCPServer
from ..models.public_mcp import PublicMCPApp, PublicMCPAppAudit
from .mcp_runtime import HTTP_MCP_TRANSPORTS


def _deleted_catalog_snapshots(
    db: Session, app_id: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """Recover catalog identities from completed, audited deletion lifecycles.

    Updates retain full before/after values. A connected server can retain
    an earlier stdio command or remote endpoint after the app launch is edited.
    Never use an update from a later recreation to identify an older deletion.
    """
    query = db.query(PublicMCPAppAudit).filter(
        PublicMCPAppAudit.action.in_(("create", "update", "delete"))
    )
    if app_id is not None:
        query = query.filter(PublicMCPAppAudit.app_id == app_id)
    rows = query.order_by(PublicMCPAppAudit.app_id, PublicMCPAppAudit.id).all()
    deleted: list[tuple[str, dict[str, Any]]] = []
    lifecycle: list[dict[str, Any]] = []
    previous_app_id: str | None = None
    for row in rows:
        if row.app_id != previous_app_id:
            lifecycle = []
            previous_app_id = row.app_id
        if row.action == "create":
            lifecycle = []
        elif row.action == "update":
            for values in (row.before_values, row.after_values):
                if isinstance(values, dict) and values.get("app_id") == row.app_id:
                    lifecycle.append(values)
        else:
            values = row.before_values
            if isinstance(values, dict) and values.get("app_id") == row.app_id:
                deleted.extend((row.app_id, snapshot) for snapshot in lifecycle)
                deleted.append((row.app_id, values))
            lifecycle = []
    return deleted


def _matches_stdio_launch(server: MCPServer, launch: dict[str, Any]) -> bool:
    command = launch.get("command")
    args = launch.get("args")
    return (
        isinstance(command, str)
        and bool(command)
        and cast(str | None, server.command) == command
        and (args is None or isinstance(args, list))
        and (cast(list[str] | None, server.args) or [])
        == (args if args is not None else [])
    )


def _matches_catalog_launch(
    server: MCPServer, app_id: str, transport: Any, launch_config: Any
) -> bool:
    """Require the historical catalog transport and launch, never just its name."""
    if (
        server.name != app_id
        or server.managed != "external"
        or not isinstance(transport, str)
        or not isinstance(server.transport, str)
        or server.transport.lower() != transport.lower()
        or not isinstance(launch_config, dict)
    ):
        return False
    if transport.lower() == "stdio":
        return _matches_stdio_launch(server, launch_config)
    if transport.lower() in HTTP_MCP_TRANSPORTS:
        # The historical remote connect path identified a catalog-owned
        # definition by its name, transport and endpoint, and could refresh
        # auth metadata in place. A missing or changed auth type must not
        # make an old public endpoint executable again.
        auth = launch_config.get("auth")
        return (
            isinstance(launch_config.get("url"), str)
            and bool(launch_config["url"])
            and isinstance(auth, dict)
            and auth.get("type") == "mcp_oauth"
            and server.url == launch_config["url"]
        )
    return False


def _live_catalog_snapshots(db: Session, app_id: str) -> list[dict[str, Any]]:
    """Include old launch versions of a still-present, edited catalog app."""
    rows = (
        db.query(PublicMCPAppAudit)
        .filter(PublicMCPAppAudit.app_id == app_id)
        .order_by(PublicMCPAppAudit.id.desc())
        .all()
    )
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        if row.action in {"create", "delete"}:
            break
        if row.action == "update":
            for values in (row.before_values, row.after_values):
                if isinstance(values, dict) and values.get("app_id") == app_id:
                    snapshots.append(values)
    return snapshots


def is_retired_catalog_server(db: Session | None, server: MCPServer) -> bool:
    """Reject catalog identities while preserving custom and migrated LAN MCP.

    Historical catalog connections have no owner; neither do some YAML-migrated
    generic servers. An unmarked row requires audited or live launch identity
    in addition to the catalog ID and absence of an owner.
    """
    if server.transport == "oauth":
        return True
    raw_auth = getattr(server, "auth", None)
    auth = raw_auth if isinstance(raw_auth, dict) else {}
    if "builtin_provenance" in auth or "app_id" in auth:
        return True
    if db is None:
        return False
    if (
        db.query(UserMCPServer.id)
        .filter(
            UserMCPServer.mcpserver_id == server.id,
            UserMCPServer.is_owner.is_(True),
        )
        .first()
        is not None
    ):
        return False
    app = (
        db.query(PublicMCPApp)
        .filter(
            (PublicMCPApp.app_id == server.name) | (PublicMCPApp.name == server.name)
        )
        .first()
    )
    if app is not None:
        if _matches_catalog_launch(
            server, str(app.app_id), app.transport, app.launch_config
        ):
            return True
        if any(
            _matches_catalog_launch(
                server,
                str(app.app_id),
                values.get("transport"),
                values.get("launch_config"),
            )
            for values in _live_catalog_snapshots(db, str(app.app_id))
        ):
            return True
    return any(
        _matches_catalog_launch(
            server, app_id, snapshot.get("transport"), snapshot.get("launch_config")
        )
        for app_id, snapshot in _deleted_catalog_snapshots(db, str(server.name))
    )


def retired_catalog_selection_names(db: Session) -> frozenset[str]:
    """Names of archived app selections without hiding user-owned MCP names."""
    catalog_rows = db.query(PublicMCPApp.app_id, PublicMCPApp.name).all()
    names = {name for row in catalog_rows for name in row if name}
    for app_id, snapshot in _deleted_catalog_snapshots(db):
        names.add(app_id)
        display_name = snapshot.get("name")
        if isinstance(display_name, str) and display_name:
            names.add(display_name)
    retired = {normalize_mcp_server_name(name) for name in names}
    if not retired:
        return frozenset()
    for server in db.query(MCPServer).all():
        normalized = normalize_mcp_server_name(str(server.name))
        if normalized in retired and not is_retired_catalog_server(db, server):
            retired.discard(normalized)
    return frozenset(retired)


def _without_retired_categories(
    categories: list[Any], retired_names: frozenset[str]
) -> list[Any]:
    return [
        category
        for category in categories
        if not (
            isinstance(category, str)
            and category.startswith("mcp:")
            and normalize_mcp_server_name(category[4:]) in retired_names
        )
    ]


def without_retired_template_connections(
    template: dict[str, Any], retired_names: frozenset[str]
) -> dict[str, Any]:
    """Project an enriched template without its archived catalog app badges."""
    if not retired_names:
        return template
    visible = dict(template)
    visible["connections"] = [
        connection
        for connection in template.get("connections", [])
        if normalize_mcp_server_name(
            connection.get("name", "") if isinstance(connection, dict) else connection
        )
        not in retired_names
    ]
    agent_config = template.get("agent_config")
    if isinstance(agent_config, dict):
        visible["agent_config"] = {
            **agent_config,
            "tool_categories": _without_retired_categories(
                agent_config.get("tool_categories", []), retired_names
            ),
        }
    workforce_config = template.get("workforce_config")
    if isinstance(workforce_config, dict):
        manager = workforce_config.get("manager")
        if isinstance(manager, dict):
            visible["workforce_config"] = {
                **workforce_config,
                "manager": {
                    **manager,
                    "tool_categories": _without_retired_categories(
                        manager.get("tool_categories") or [], retired_names
                    ),
                },
            }
    return visible
