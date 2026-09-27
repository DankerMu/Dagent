"""
MCP Server Management API Endpoints

Provides REST API endpoints for managing MCP server configurations
in the web application.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Union,
    cast,
)
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...config import get_app_base_url, get_public_api_base_url, get_session_secret
from ...core.tools.adapters.vibe.connector_runtime import (
    validate_runtime_config_declaration,
)
from ...core.tools.core.mcp.data_config import MCPServerConfig
from ...core.tools.core.mcp.manager.db import DatabaseMCPServerManager
from ...core.tools.core.mcp.model import MASKED_SECRET_VALUE, SENSITIVE_AUTH_FIELDS
from ...core.utils.encryption import decrypt_value, encrypt_value
from ..auth_dependencies import get_current_user, is_admin_user
from ..models.custom_api import CustomApi, UserCustomApi
from ..models.database import get_db
from ..models.mcp import MCPServer, UserMCPServer
from ..models.mcp_oauth import (
    MCPOAuthClient,
    MCPOAuthFlowState,
    MCPOAuthGrant,
    mcp_oauth_client_lookup_hash,
    mcp_oauth_client_registration_lookup_hash,
    mcp_oauth_grant_lookup_hash,
)
from ..models.user import User
from ..services.mcp_oauth import (
    MCP_OAUTH_HTTP_TIMEOUT_SECONDS,
    MCP_OAUTH_PERSISTED_VALUE_MAX_LENGTH,
    MCP_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH,
    MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD_MAX_LENGTH,
    MCP_OAUTH_TOKEN_TYPE_MAX_LENGTH,
    MCPOAuthDiscoveryError,
    _same_url,
    create_mcp_oauth_http_client,
    discover_mcp_oauth_metadata,
    normalize_mcp_oauth_scope,
    oauth_error_log_payload,
    oauth_error_message,
    oauth_exception_message,
    oauth_post,
    oauth_token_expires_at,
    register_mcp_oauth_public_client,
    select_mcp_oauth_grants,
    validate_mcp_oauth_persisted_value,
)
from ..services.mcp_runtime import HTTP_MCP_TRANSPORTS
from ..services.retired_mcp_catalog import is_retired_catalog_server
from ..services.user_oauth import (
    normalize_user_oauth_resource_owner_key,
)

logger = logging.getLogger(__name__)

MCP_OAUTH_STATE_COOKIE = "xagent_mcp_oauth_state"
MCP_OAUTH_STATE_TTL = timedelta(minutes=10)
MCP_OAUTH_STATE_COOKIE_MAX_AGE_SECONDS = int(MCP_OAUTH_STATE_TTL.total_seconds())
# How long an expired mcp_oauth_flow_states row is kept before the sweep in
# connect_mcp_oauth deletes it. A row is already unusable the moment it expires
# — _claim_mcp_oauth_flow_state requires expires_at > now — so this grace
# period exists only to keep the sweep well clear of rows a callback may still
# be racing to claim, and to leave a recent trail for debugging a failed
# authorization. Matches the Slack OAuth flow-state ledger's retention.
MCP_OAUTH_FLOW_STATE_RETENTION = timedelta(days=1)
# Most stale rows the sweep in connect_mcp_oauth removes per request, so a
# first-deploy backlog cannot turn one connect into a long transaction.
MCP_OAUTH_FLOW_STATE_SWEEP_BATCH = 1000
MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHODS = frozenset(
    {"none", "client_secret_post", "client_secret_basic"}
)


# Pydantic models for API
class MCPServerCreate(BaseModel):
    """Request model for creating MCP server."""

    name: str = Field(..., min_length=1, max_length=100, description="Server name")
    transport: str = Field(
        ..., description="Transport type (stdio, sse, websocket, streamable_http)"
    )
    description: Optional[str] = Field(None, description="Server description")
    config: dict = Field(..., description="Transport-specific configuration")
    is_active: bool = Field(True, description="Whether the server is active")
    user_env: Optional[dict] = Field(
        None, description="Per-user env overrides (merged over global env at runtime)"
    )
    runtime_input_schema: Optional[dict] = Field(
        None, description="Runtime input declarations"
    )
    runtime_bindings: Optional[list[dict]] = Field(
        None, description="Runtime binding declarations"
    )
    allow_delegated_authorization: bool = Field(
        False, description="Allow runtime Authorization header binding"
    )


class MCPServerUpdate(BaseModel):
    """Request model for updating MCP server."""

    name: Optional[str] = Field(
        None, min_length=1, max_length=100, description="Server name"
    )
    transport: Optional[str] = Field(None, description="Transport type")
    description: Optional[str] = Field(None, description="Server description")
    config: Optional[dict] = Field(None, description="Transport-specific configuration")
    is_active: Optional[bool] = Field(None, description="Whether the server is active")
    user_env: Optional[dict] = Field(
        None, description="Per-user env overrides (merged over global env at runtime)"
    )
    runtime_input_schema: Optional[dict] = Field(
        None, description="Runtime input declarations"
    )
    runtime_bindings: Optional[list[dict]] = Field(
        None, description="Runtime binding declarations"
    )
    allow_delegated_authorization: Optional[bool] = Field(
        None, description="Allow runtime Authorization header binding"
    )


class MCPServerResponse(BaseModel):
    """Response model for MCP server."""

    id: int
    user_id: int
    name: str
    transport: str
    description: Optional[str]
    config: dict
    is_active: bool
    is_default: bool
    user_env: Optional[dict]
    env_source: Optional[Literal["own", "shared", "platform"]] = None
    runtime_input_schema: Optional[dict]
    runtime_bindings: Optional[list[dict]]
    allow_delegated_authorization: bool
    can_edit_global: bool
    transport_display: str
    created_at: Optional[str]
    updated_at: Optional[str]

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class MCPConnectionTest(BaseModel):
    """Request model for testing MCP connection."""

    name: str = Field(..., description="Connection name")
    transport: str = Field(..., description="Transport type")
    config: dict[str, Any] = Field(..., description="Connection configuration")


class MCPConnectionTestResponse(BaseModel):
    """Response model for MCP connection test."""

    success: bool
    message: str
    details: Optional[dict] = None


@dataclass(frozen=True)
class _MCPToolLoadAPIProjection:
    """Public-safe API projection that preserves usable partial load results."""

    tools: tuple[Any, ...]
    failure_message: str
    failures: tuple[dict[str, Any], ...]


def _project_mcp_tool_load_result(load_result: Any) -> _MCPToolLoadAPIProjection:
    """Project one structured load without collapsing partial success."""
    from ...core.tools.adapters.vibe.mcp_adapter import (
        MCPFailurePhase,
        MCPServerLoadFailure,
        mcp_load_failure_message,
    )

    failures = [
        failure
        for failure in getattr(load_result, "failures", ())
        if isinstance(failure, MCPServerLoadFailure)
    ]
    failure_message = mcp_load_failure_message(
        failures[0].phase if failures else MCPFailurePhase.NO_TOOLS_RETURNED
    )
    return _MCPToolLoadAPIProjection(
        tools=tuple(getattr(load_result, "tools", ())),
        failure_message=failure_message,
        failures=tuple(
            {
                "server_name": failure.server_name,
                "phase": failure.phase.value,
                "attempts": failure.attempts,
            }
            for failure in failures
        ),
    )


class MCPOAuthDiscoverRequest(BaseModel):
    """Request model for MCP OAuth metadata discovery."""

    model_config = ConfigDict(extra="forbid")


class MCPOAuthDiscoverResponse(BaseModel):
    """Selected MCP OAuth metadata for a configured MCP server."""

    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: list[str]
    authorization_servers: list[str]
    client_id_metadata_document_supported: bool


class MCPOAuthConnectRequest(MCPOAuthDiscoverRequest):
    """Request model for starting MCP OAuth Authorization Code + PKCE."""

    redirect_after: Optional[str] = None


class MCPOAuthGrantResponse(BaseModel):
    """Public-safe MCP OAuth grant status."""

    id: int
    resource_owner_key: str
    issuer: str
    resource: str
    scope: str
    token_type: str
    status: str
    expires_at: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    revoked_at: Optional[str]


class MCPOAuthStatusResponse(BaseModel):
    """MCP OAuth connection status for the current user."""

    server_id: int
    auth_type: Optional[str]
    resource: Optional[str]
    issuer: Optional[str]
    scope: Optional[str]
    grants: list[MCPOAuthGrantResponse]


# Create router
mcp_router = APIRouter(prefix="/api/mcp", tags=["MCP Management"])


class ConfigFieldParser:
    """Modular parser for configuration fields with type-specific parsing strategies."""

    @staticmethod
    def parse_string_list(value: str) -> List[str]:
        """Parse a string into a list of strings."""
        try:
            # Try JSON first
            result = json.loads(value)
            if isinstance(result, list):
                return result
            raise ValueError("Not a list")
        except (json.JSONDecodeError, ValueError):
            try:
                # Try to parse as shell command line
                return shlex.split(value)
            except ValueError:
                # Fall back to splitting by whitespace and newlines
                return [
                    arg.strip()
                    for arg in value.replace("\n", " ").split()
                    if arg.strip()
                ]

    @staticmethod
    def parse_key_value_dict(value: str) -> Dict[str, str]:
        """Parse a string into a dictionary of key-value pairs."""
        try:
            # Try JSON first
            result = json.loads(value)
            if isinstance(result, dict):
                return result
            raise ValueError("Not a dictionary")
        except (json.JSONDecodeError, ValueError):
            # Parse as key=value pairs (one per line or space-separated)
            result = {}
            lines = value.replace("\n", " ").split()
            for line in lines:
                if "=" in line:
                    key, val = line.split("=", 1)
                    result[key.strip()] = val.strip()
            return result

    @staticmethod
    def parse_port_mappings(value: str) -> Dict[str, Union[int, str]]:
        """Parse port mappings as container_port:host_port."""
        try:
            # Try JSON first
            result = json.loads(value)
            if isinstance(result, dict):
                return result
            raise ValueError("Not a dictionary")
        except (json.JSONDecodeError, ValueError):
            # Parse as port:port pairs
            result = {}
            lines = value.replace("\n", " ").split()
            for line in lines:
                if ":" in line:
                    container_port, host_port = line.split(":", 1)
                    result[container_port.strip()] = host_port.strip()
            return result

    @staticmethod
    def parse_boolean(value: str) -> bool:
        """Parse a string into a boolean."""
        return value.lower() in ("true", "1", "yes", "on")

    @staticmethod
    def parse_json_or_fallback(
        value: str, fallback_parser: Callable[[Any], Any] | None = None
    ) -> Any:
        """Try to parse as JSON, fall back to another parser if provided."""
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            if fallback_parser:
                return fallback_parser(value)
            return value


def _format_optional_datetime(value: object) -> Optional[str]:
    """Serialize datetimes while tolerating ORM attributes without DB timestamps."""
    return value.isoformat() if isinstance(value, datetime) else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _default_mcp_oauth_redirect_uri() -> str:
    base_url = (
        get_public_api_base_url() or get_app_base_url() or "http://localhost:8000"
    )
    return f"{base_url.rstrip('/')}/api/mcp/oauth/callback"


def _safe_mcp_oauth_redirect_after(value: str | None) -> str:
    if not value:
        return "/tools"
    parsed = urlsplit(value)
    if (
        parsed.scheme
        or parsed.netloc
        or not value.startswith("/")
        or value.startswith("//")
        or value.startswith("/\\")
        or len(value) > MCP_OAUTH_PERSISTED_VALUE_MAX_LENGTH
    ):
        return "/tools"
    return value


def _mcp_oauth_redirect_after_url(value: str | None) -> str:
    redirect_after = _safe_mcp_oauth_redirect_after(value)
    app_base_url = get_app_base_url()
    if app_base_url:
        return f"{app_base_url}{redirect_after}"
    return redirect_after


def _mcp_oauth_cookie_secure() -> bool:
    base_url = get_app_base_url()
    return bool(base_url and base_url.lower().startswith("https://"))


def _mcp_oauth_state_cookie_signature(state_value: str) -> str:
    return hmac.new(
        get_session_secret().encode("utf-8"),
        state_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _mcp_oauth_state_cookie_value(state_value: str) -> str:
    return f"{state_value}.{_mcp_oauth_state_cookie_signature(state_value)}"


def _set_mcp_oauth_state_cookie(response: Response, state_value: str) -> None:
    response.set_cookie(
        MCP_OAUTH_STATE_COOKIE,
        _mcp_oauth_state_cookie_value(state_value),
        max_age=MCP_OAUTH_STATE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_mcp_oauth_cookie_secure(),
        samesite="lax",
        path="/api/mcp",
    )


def _clear_mcp_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(MCP_OAUTH_STATE_COOKIE, path="/api/mcp")


def _redirect_after_with_params(
    raw_redirect_after: str | None, params: tuple[tuple[str, str], ...]
) -> str:
    redirect_after = _safe_mcp_oauth_redirect_after(raw_redirect_after)
    parts = urlsplit(redirect_after)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _mcp_oauth_callback_error_redirect(
    flow_state: MCPOAuthFlowState,
    *,
    error_code: str,
    message: str,
) -> RedirectResponse:
    raw_redirect_after = (
        str(flow_state.redirect_after) if flow_state.redirect_after else None
    )
    return _mcp_oauth_callback_error_redirect_for_path(
        raw_redirect_after,
        error_code=error_code,
        message=message,
    )


def _mcp_oauth_callback_error_redirect_for_path(
    raw_redirect_after: str | None,
    *,
    error_code: str,
    message: str,
) -> RedirectResponse:
    redirect_path = _redirect_after_with_params(
        raw_redirect_after,
        (
            ("mcp_oauth_error", error_code),
            (
                "mcp_oauth_error_message",
                oauth_error_message(message, "MCP OAuth authorization failed"),
            ),
        ),
    )
    response = RedirectResponse(_mcp_oauth_redirect_after_url(redirect_path))
    _clear_mcp_oauth_state_cookie(response)
    return response


def _validate_mcp_oauth_state_cookie(request: Request, state_value: str) -> None:
    cookie_value = request.cookies.get(MCP_OAUTH_STATE_COOKIE)
    if not cookie_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_state",
                "message": "OAuth callback state was not initiated by this browser session",
            },
        )
    try:
        cookie_state, cookie_signature = cookie_value.rsplit(".", 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_state", "message": "Invalid OAuth state cookie"},
        ) from exc
    expected_signature = _mcp_oauth_state_cookie_signature(cookie_state)
    if not (
        hmac.compare_digest(cookie_state, state_value)
        and hmac.compare_digest(cookie_signature, expected_signature)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_state",
                "message": "OAuth callback state did not match this browser session",
            },
        )


def _default_resource_owner_key(user_id: int) -> str:
    return f"xagent:user:{user_id}"


def _has_actor_owned_mcp_oauth_state(
    db: Session,
    *,
    server_id: int,
    user_id: int,
) -> bool:
    """Whether generic deletion would cross an actor ownership boundary."""
    default_owner = _default_resource_owner_key(user_id)
    actor_grant = (
        db.query(MCPOAuthGrant.id)
        .filter(
            MCPOAuthGrant.mcp_server_id == server_id,
            MCPOAuthGrant.user_id == user_id,
            MCPOAuthGrant.resource_owner_key != default_owner,
            MCPOAuthGrant.status == "active",
        )
        .first()
    )
    if actor_grant is not None:
        return True

    return (
        db.query(MCPOAuthFlowState.id)
        .filter(
            MCPOAuthFlowState.mcp_server_id == server_id,
            MCPOAuthFlowState.user_id == user_id,
            MCPOAuthFlowState.resource_owner_key != default_owner,
            MCPOAuthFlowState.expires_at > _utc_now(),
        )
        .first()
        is not None
    )


def _oauth_authorization_url(endpoint: str, params: dict[str, str]) -> str:
    parts = urlsplit(endpoint)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _scope_string(scopes: list[str] | tuple[str, ...] | str | None) -> str:
    try:
        return normalize_mcp_oauth_scope(scopes)
    except MCPOAuthDiscoveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _bounded_mcp_oauth_value(
    value: str,
    *,
    field_name: str,
    max_length: int = MCP_OAUTH_PERSISTED_VALUE_MAX_LENGTH,
) -> str:
    try:
        return validate_mcp_oauth_persisted_value(
            value, field_name=field_name, max_length=max_length
        )
    except MCPOAuthDiscoveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _get_user_mcp_server_or_404(
    db: Session, *, user_id: int, server_id: int, require_active: bool = False
) -> tuple[UserMCPServer, MCPServer]:
    query = (
        db.query(UserMCPServer, MCPServer)
        .join(MCPServer, UserMCPServer.mcpserver_id == MCPServer.id)
        .filter(UserMCPServer.user_id == user_id, MCPServer.id == server_id)
    )
    if require_active:
        query = query.filter(UserMCPServer.is_active)
    result = query.first()
    if not result or is_retired_catalog_server(db, result[1]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found"
        )
    return cast(tuple[UserMCPServer, MCPServer], result)


def _get_mcp_oauth_config(server: MCPServer) -> dict[str, Any]:
    config = server.to_config_dict()
    auth_config = config.get("auth")
    if not isinstance(auth_config, dict) or auth_config.get("type") != "mcp_oauth":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MCP server is not configured for MCP OAuth",
        )
    if server.transport not in HTTP_MCP_TRANSPORTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MCP OAuth is only supported for HTTP MCP transports",
        )
    if not server.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MCP OAuth server requires a URL",
        )
    return auth_config


def _configured_mcp_oauth_value(
    request_value: str | None, auth_config: dict[str, Any], key: str
) -> str | None:
    value = request_value if request_value is not None else auth_config.get(key)
    return str(value).strip() if value else None


async def _discover_mcp_oauth_for_server(
    server: MCPServer,
    auth_config: dict[str, Any],
) -> Any:
    if not server.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MCP OAuth server requires a URL",
        )
    try:
        return await discover_mcp_oauth_metadata(
            str(server.url),
            headers=None,
            configured_resource_metadata_url=_configured_mcp_oauth_value(
                None,
                auth_config,
                "resource_metadata_url",
            ),
            configured_issuer=_configured_mcp_oauth_value(None, auth_config, "issuer"),
            configured_resource=_configured_mcp_oauth_value(
                None, auth_config, "resource"
            ),
        )
    except MCPOAuthDiscoveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _mcp_oauth_discovery_response(discovery: Any) -> MCPOAuthDiscoverResponse:
    return MCPOAuthDiscoverResponse(
        resource=discovery.resource,
        issuer=discovery.authorization_server.issuer,
        authorization_endpoint=discovery.authorization_server.authorization_endpoint,
        token_endpoint=discovery.authorization_server.token_endpoint,
        scopes=list(discovery.scopes),
        authorization_servers=list(discovery.protected_resource.authorization_servers),
        client_id_metadata_document_supported=(
            discovery.authorization_server.client_id_metadata_document_supported
        ),
    )


def _upsert_mcp_oauth_client(
    db: Session,
    *,
    server_id: int,
    discovery: Any,
    client_id: str,
    client_secret: str | None,
    token_endpoint_auth_method: str,
    redirect_uri: str,
    registration_lookup_hash: str | None = None,
) -> MCPOAuthClient:
    issuer = _bounded_mcp_oauth_value(
        str(discovery.authorization_server.issuer), field_name="issuer"
    )
    authorization_endpoint = _bounded_mcp_oauth_value(
        str(discovery.authorization_server.authorization_endpoint),
        field_name="authorization_endpoint",
    )
    token_endpoint = _bounded_mcp_oauth_value(
        str(discovery.authorization_server.token_endpoint), field_name="token_endpoint"
    )
    client_id = _bounded_mcp_oauth_value(client_id, field_name="client_id")
    token_endpoint_auth_method = _bounded_mcp_oauth_value(
        token_endpoint_auth_method,
        field_name="token_endpoint_auth_method",
        max_length=MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD_MAX_LENGTH,
    )
    redirect_uri = _bounded_mcp_oauth_value(redirect_uri, field_name="redirect_uri")
    lookup_hash = mcp_oauth_client_lookup_hash(server_id, issuer, client_id)

    def load_existing_client() -> MCPOAuthClient | None:
        if registration_lookup_hash:
            registered_client = (
                db.query(MCPOAuthClient)
                .filter(
                    MCPOAuthClient.registration_lookup_hash == registration_lookup_hash,
                )
                .first()
            )
            if registered_client is not None:
                return registered_client
        return (
            db.query(MCPOAuthClient)
            .filter(
                MCPOAuthClient.lookup_hash == lookup_hash,
            )
            .first()
        )

    def apply_client_values(existing: MCPOAuthClient | None) -> MCPOAuthClient:
        encrypted_client_secret: str | None
        if client_secret == MASKED_SECRET_VALUE:
            if existing is None or not existing.client_secret:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_resource",
                        "message": "Masked MCP OAuth client_secret has no stored value",
                    },
                )
            encrypted_client_secret = str(existing.client_secret)
        else:
            encrypted_client_secret = (
                encrypt_value(client_secret) if client_secret else None
            )
        client = existing or MCPOAuthClient(
            mcp_server_id=server_id,
            lookup_hash=lookup_hash,
            registration_lookup_hash=registration_lookup_hash,
            issuer=issuer,
            client_id=client_id,
        )
        setattr(client, "authorization_endpoint", authorization_endpoint)
        setattr(client, "token_endpoint", token_endpoint)
        setattr(client, "client_secret", encrypted_client_secret)
        setattr(client, "token_endpoint_auth_method", token_endpoint_auth_method)
        setattr(client, "redirect_uri", redirect_uri)
        setattr(client, "metadata_json", discovery.authorization_server.raw)
        if existing is None:
            db.add(client)
        return client

    try:
        with db.begin_nested():
            client = apply_client_values(load_existing_client())
            db.flush()
        return client
    except IntegrityError as exc:
        existing_after_conflict = load_existing_client()
        if existing_after_conflict is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "oauth_client_conflict",
                    "message": "MCP OAuth client configuration changed concurrently",
                },
            ) from exc
        with db.begin_nested():
            client = apply_client_values(existing_after_conflict)
            db.flush()
        return client


class _OAuthPersistence(Enum):
    COMMIT = "commit"
    CALLER = "caller"


class _SQLiteOAuthPersistenceTransactionError(RuntimeError):
    """The caller has SQLite writes that the persistence fence cannot reset."""


def _begin_sqlite_oauth_persistence(db: Session) -> None:
    """Acquire SQLite write intent before lifecycle identity is re-read."""
    if db.new or db.dirty or db.deleted:
        raise _SQLiteOAuthPersistenceTransactionError(
            "SQLite OAuth persistence requires a read-only preflight"
        )

    connection = db.connection()
    driver_connection = connection.connection.driver_connection
    if bool(getattr(driver_connection, "in_transaction", False)):
        raise _SQLiteOAuthPersistenceTransactionError(
            "SQLite OAuth persistence requires an owned transaction"
        )

    # SQLAlchemy has logically autobegun for the read-only preflight even
    # though pysqlite has not emitted BEGIN. End that logical transaction,
    # then reserve SQLite's single writer before the identity reads.
    db.rollback()
    connection = db.connection()
    driver_connection = connection.connection.driver_connection
    if bool(getattr(driver_connection, "in_transaction", False)):
        raise RuntimeError("SQLite OAuth persistence could not reset preflight")
    connection.exec_driver_sql("BEGIN IMMEDIATE")


@dataclass(frozen=True)
class _MCPOAuthAssociationIdentity:
    server_id: int
    user_id: int
    lifecycle_generation: UUID


@dataclass(frozen=True)
class _MCPOAuthFlowIdentity:
    id: int
    state: str
    server_id: int
    user_id: int
    client_id: int
    association_lifecycle_generation: UUID


def _mcp_oauth_flow_identity(flow_state: MCPOAuthFlowState) -> _MCPOAuthFlowIdentity:
    generation = flow_state.association_lifecycle_generation
    if not isinstance(generation, UUID):
        raise RuntimeError("MCP OAuth flow has no association lifecycle generation")
    return _MCPOAuthFlowIdentity(
        id=int(flow_state.id),
        state=str(flow_state.state),
        server_id=int(flow_state.mcp_server_id),
        user_id=int(flow_state.user_id),
        client_id=int(flow_state.mcp_oauth_client_id),
        association_lifecycle_generation=generation,
    )


def _lock_active_mcp_oauth_lifecycle(
    db: Session,
    *,
    association_identity: _MCPOAuthAssociationIdentity,
    flow_identity: _MCPOAuthFlowIdentity | None = None,
    association_must_be_active: bool = True,
) -> tuple[MCPServer, UserMCPServer, MCPOAuthFlowState | None] | None:
    """Lock server, exact association generation, then exact flow if supplied."""
    if db.get_bind().dialect.name == "sqlite":
        _begin_sqlite_oauth_persistence(db)
    db.expire_all()

    server = (
        db.query(MCPServer)
        .filter(MCPServer.id == association_identity.server_id)
        .with_for_update()
        .one_or_none()
    )
    if server is None or is_retired_catalog_server(db, server):
        return None
    association_query = db.query(UserMCPServer).filter(
        UserMCPServer.user_id == association_identity.user_id,
        UserMCPServer.mcpserver_id == association_identity.server_id,
        UserMCPServer.lifecycle_generation == association_identity.lifecycle_generation,
    )
    if association_must_be_active:
        association_query = association_query.filter(UserMCPServer.is_active.is_(True))
    association = association_query.with_for_update().one_or_none()
    if association is None:
        return None

    flow_state: MCPOAuthFlowState | None = None
    if flow_identity is not None:
        if (
            flow_identity.server_id != association_identity.server_id
            or flow_identity.user_id != association_identity.user_id
            or flow_identity.association_lifecycle_generation
            != association_identity.lifecycle_generation
        ):
            return None
        flow_state = (
            db.query(MCPOAuthFlowState)
            .filter(
                MCPOAuthFlowState.id == flow_identity.id,
                MCPOAuthFlowState.state == flow_identity.state,
                MCPOAuthFlowState.mcp_server_id == flow_identity.server_id,
                MCPOAuthFlowState.user_id == flow_identity.user_id,
                MCPOAuthFlowState.mcp_oauth_client_id == flow_identity.client_id,
                MCPOAuthFlowState.association_lifecycle_generation
                == flow_identity.association_lifecycle_generation,
            )
            .with_for_update()
            .one_or_none()
        )
        if flow_state is None:
            return None
    return server, association, flow_state


def _lock_caller_oauth_lifecycle(
    db: Session,
    *,
    association_identity: _MCPOAuthAssociationIdentity,
) -> tuple[MCPServer, UserMCPServer] | None:
    """Lock and activate a caller-owned lifecycle after provider I/O."""
    db.flush()
    server = (
        db.query(MCPServer)
        .filter(MCPServer.id == association_identity.server_id)
        .with_for_update()
        .one_or_none()
    )
    if server is None or is_retired_catalog_server(db, server):
        return None
    association = (
        db.query(UserMCPServer)
        .filter(
            UserMCPServer.user_id == association_identity.user_id,
            UserMCPServer.mcpserver_id == association_identity.server_id,
            UserMCPServer.lifecycle_generation
            == association_identity.lifecycle_generation,
        )
        .with_for_update()
        .one_or_none()
    )
    if association is None:
        return None
    if not association.is_active:
        setattr(association, "is_active", True)
        db.flush()
    return server, association


def _sweep_expired_mcp_oauth_flow_states(db: Session) -> None:
    """Delete one bounded batch of dead flow states outside lifecycle locks."""
    # Sweep this table's dead rows before adding another, mirroring the Slack
    # OAuth flow-state ledger in channel.py. Every abandoned, denied or
    # double-submitted authorization leaves a row that is permanently unusable
    # once it expires (the claim query requires consumed_at IS NULL and
    # expires_at > now), but nothing else removes it until a disconnect or a
    # server cascade. Deliberately global so users who never reconnect do not
    # retain rows forever, and bounded so one user-facing request does not drain
    # an unbounded historical backlog.
    try:
        stale_flow_state_ids = (
            db.query(MCPOAuthFlowState.id)
            .filter(
                MCPOAuthFlowState.expires_at
                < _utc_now() - MCP_OAUTH_FLOW_STATE_RETENTION
            )
            .order_by(MCPOAuthFlowState.id)
            .with_for_update(skip_locked=True)
            .limit(MCP_OAUTH_FLOW_STATE_SWEEP_BATCH)
            .scalar_subquery()
        )
        db.query(MCPOAuthFlowState).filter(
            MCPOAuthFlowState.id.in_(stale_flow_state_ids)
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def _persist_mcp_oauth_connect_flow(
    db: Session,
    *,
    association_identity: _MCPOAuthAssociationIdentity,
    discovery: Any,
    client_id: str,
    client_secret: str | None,
    token_endpoint_auth_method: str,
    redirect_uri: str,
    registration_lookup_hash: str | None,
    resource_owner_key: str,
    selected_issuer: str,
    selected_resource: str,
    selected_scope: str,
    redirect_after: str | None,
    persistence: _OAuthPersistence = _OAuthPersistence.COMMIT,
) -> tuple[str, str, str] | None:
    """Persist a client and flow only for the preflight association generation."""
    if persistence is _OAuthPersistence.COMMIT:
        # Commit independent maintenance before taking lifecycle row locks.
        _sweep_expired_mcp_oauth_flow_states(db)
        lifecycle = _lock_active_mcp_oauth_lifecycle(
            db,
            association_identity=association_identity,
        )
    else:
        caller_lifecycle = _lock_caller_oauth_lifecycle(
            db,
            association_identity=association_identity,
        )
        lifecycle = (*caller_lifecycle, None) if caller_lifecycle is not None else None
    if lifecycle is None:
        if persistence is _OAuthPersistence.COMMIT:
            db.rollback()
        return None

    try:
        oauth_client = _upsert_mcp_oauth_client(
            db,
            server_id=association_identity.server_id,
            discovery=discovery,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=token_endpoint_auth_method,
            redirect_uri=redirect_uri,
            registration_lookup_hash=registration_lookup_hash,
        )

        state_value = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        db.add(
            MCPOAuthFlowState(
                state=state_value,
                mcp_server_id=association_identity.server_id,
                user_id=association_identity.user_id,
                association_lifecycle_generation=(
                    association_identity.lifecycle_generation
                ),
                mcp_oauth_client_id=oauth_client.id,
                resource_owner_key=resource_owner_key,
                issuer=selected_issuer,
                resource=selected_resource,
                scope=selected_scope,
                code_verifier=encrypt_value(code_verifier),
                redirect_after=_safe_mcp_oauth_redirect_after(redirect_after),
                expires_at=_utc_now() + MCP_OAUTH_STATE_TTL,
            )
        )
        persisted_client_id = str(oauth_client.client_id)
        if persistence is _OAuthPersistence.COMMIT:
            db.commit()
        else:
            db.flush()
        return persisted_client_id, state_value, code_verifier
    except Exception:
        if persistence is _OAuthPersistence.COMMIT:
            db.rollback()
        raise


def _mcp_oauth_grant_response(grant: MCPOAuthGrant) -> MCPOAuthGrantResponse:
    return MCPOAuthGrantResponse(
        id=cast(int, grant.id),
        resource_owner_key=str(grant.resource_owner_key),
        issuer=str(grant.issuer),
        resource=str(grant.resource),
        scope=str(grant.scope),
        token_type=str(grant.token_type),
        status=str(grant.status),
        expires_at=_format_optional_datetime(grant.expires_at),
        created_at=_format_optional_datetime(grant.created_at),
        updated_at=_format_optional_datetime(grant.updated_at),
        revoked_at=_format_optional_datetime(grant.revoked_at),
    )


def _validate_mcp_oauth_callback_issuer(
    *,
    request: Request,
    client: MCPOAuthClient,
    flow_state: MCPOAuthFlowState,
) -> None:
    metadata: dict[str, Any] = (
        client.metadata_json if isinstance(client.metadata_json, dict) else {}
    )
    issuer_required = (
        metadata.get("authorization_response_iss_parameter_supported") is True
    )
    response_issuer = request.query_params.get("iss")
    expected_issuer = str(flow_state.issuer)

    if response_issuer is None:
        if issuer_required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "issuer_mismatch",
                    "message": "Authorization response issuer is required",
                },
            )
        return

    if not _same_url(response_issuer, expected_issuer):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "issuer_mismatch",
                "message": "Authorization response issuer did not match flow state",
            },
        )


def _mcp_oauth_flow_state_error(
    db: Session, flow_state: MCPOAuthFlowState
) -> tuple[str, str] | None:
    if flow_state.consumed_at is not None:
        return "state_already_consumed", "OAuth state consumed"
    if _as_aware_utc(flow_state.expires_at) <= _utc_now():
        return "expired_state", "OAuth state expired"
    association_generation = flow_state.association_lifecycle_generation
    if association_generation is None:
        return (
            "invalid_state",
            "OAuth state is not bound to an MCP server connection lifecycle",
        )
    if (
        db.query(UserMCPServer)
        .filter(
            UserMCPServer.user_id == flow_state.user_id,
            UserMCPServer.mcpserver_id == flow_state.mcp_server_id,
            UserMCPServer.lifecycle_generation == association_generation,
            UserMCPServer.is_active.is_(True),
        )
        .first()
        is None
    ):
        return (
            "invalid_state",
            "OAuth state is no longer associated with MCP server access",
        )
    return None


def _claim_mcp_oauth_flow_state(
    db: Session, flow_state: MCPOAuthFlowState
) -> tuple[str, str] | None:
    claimed_at = _utc_now()
    updated = (
        db.query(MCPOAuthFlowState)
        .filter(
            MCPOAuthFlowState.id == flow_state.id,
            MCPOAuthFlowState.state == flow_state.state,
            MCPOAuthFlowState.mcp_server_id == flow_state.mcp_server_id,
            MCPOAuthFlowState.user_id == flow_state.user_id,
            MCPOAuthFlowState.mcp_oauth_client_id == flow_state.mcp_oauth_client_id,
            MCPOAuthFlowState.association_lifecycle_generation
            == flow_state.association_lifecycle_generation,
            MCPOAuthFlowState.consumed_at.is_(None),
            MCPOAuthFlowState.expires_at > claimed_at,
        )
        .update(
            {MCPOAuthFlowState.consumed_at: claimed_at},
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.rollback()
        return "state_already_consumed", "OAuth state consumed"
    db.commit()
    return None


async def _exchange_mcp_oauth_code(
    *,
    client: MCPOAuthClient,
    code: str,
    code_verifier: str,
    resource: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    auth: httpx.Auth | None = None
    client_secret = (
        decrypt_value(str(client.client_secret)) if client.client_secret else ""
    )
    auth_method = str(client.token_endpoint_auth_method or "none")
    if auth_method == "client_secret_post" and client_secret:
        data["client_secret"] = client_secret
    elif auth_method == "client_secret_basic" and client_secret:
        auth = httpx.BasicAuth(str(client.client_id), client_secret)
    elif auth_method not in {"none", "client_secret_post", "client_secret_basic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unsupported_auth_server",
                "message": f"Unsupported token endpoint auth method: {auth_method}",
            },
        )

    try:
        post_kwargs: dict[str, Any] = {
            "data": data,
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        }
        if auth is not None:
            post_kwargs["auth"] = auth
        async with create_mcp_oauth_http_client(
            timeout=MCP_OAUTH_HTTP_TIMEOUT_SECONDS,
        ) as http_client:
            response = await oauth_post(
                str(client.token_endpoint),
                client=http_client,
                **post_kwargs,
            )
        payload = response.json()
    except (MCPOAuthDiscoveryError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "token_exchange_failed",
                "message": oauth_exception_message(
                    exc, "MCP OAuth token exchange failed"
                ),
            },
        ) from exc

    if (
        response.status_code >= 400
        or not isinstance(payload, dict)
        or payload.get("error")
    ):
        logger.warning(
            "MCP OAuth token exchange failed with token endpoint payload: %s",
            oauth_error_log_payload(payload),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "token_exchange_failed",
                "message": oauth_error_message(
                    payload, "MCP OAuth token exchange failed"
                ),
            },
        )
    if not payload.get("access_token"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "token_exchange_failed",
                "message": "Token response did not include access_token",
            },
        )
    return payload


@dataclass(frozen=True)
class _MCPOAuthIssuedTokenSnapshot:
    flow_id: int
    revocation_endpoint: str | None
    client_id: str
    encrypted_client_secret: str | None
    token_endpoint_auth_method: str
    access_token: str
    refresh_token: str | None


def _mcp_oauth_issued_token_snapshot(
    *,
    client: MCPOAuthClient,
    flow_id: int,
    token_data: dict[str, Any],
) -> _MCPOAuthIssuedTokenSnapshot:
    metadata: dict[str, Any] = (
        client.metadata_json if isinstance(client.metadata_json, dict) else {}
    )
    revocation_endpoint = metadata.get("revocation_endpoint")
    refresh_token = token_data.get("refresh_token")
    return _MCPOAuthIssuedTokenSnapshot(
        flow_id=flow_id,
        revocation_endpoint=(
            revocation_endpoint
            if isinstance(revocation_endpoint, str) and revocation_endpoint
            else None
        ),
        client_id=str(client.client_id),
        encrypted_client_secret=(
            str(client.client_secret) if client.client_secret else None
        ),
        token_endpoint_auth_method=str(client.token_endpoint_auth_method or "none"),
        access_token=str(token_data["access_token"]),
        refresh_token=str(refresh_token) if refresh_token else None,
    )


async def _revoke_mcp_oauth_issued_token_externally(
    snapshot: _MCPOAuthIssuedTokenSnapshot,
) -> None:
    """Best-effort revoke a token whose final local persistence failed."""
    if snapshot.revocation_endpoint is None:
        return
    try:
        client_secret = (
            decrypt_value(snapshot.encrypted_client_secret)
            if snapshot.encrypted_client_secret
            else ""
        )
    except Exception:
        logger.warning(
            "Skipping issued MCP OAuth token revocation for flow %s because "
            "client credentials are unavailable",
            snapshot.flow_id,
        )
        return

    auth: httpx.Auth | None = None
    data_base: dict[str, str] = {"client_id": snapshot.client_id}
    if snapshot.token_endpoint_auth_method == "client_secret_post" and client_secret:
        data_base["client_secret"] = client_secret
    elif snapshot.token_endpoint_auth_method == "client_secret_basic" and client_secret:
        auth = httpx.BasicAuth(snapshot.client_id, client_secret)
    elif snapshot.token_endpoint_auth_method not in {
        "none",
        "client_secret_post",
        "client_secret_basic",
    }:
        logger.warning(
            "Skipping issued MCP OAuth token revocation for flow %s because "
            "the client authentication method is unsupported",
            snapshot.flow_id,
        )
        return

    try:
        async with create_mcp_oauth_http_client(
            timeout=MCP_OAUTH_HTTP_TIMEOUT_SECONDS,
        ) as http_client:
            for token, token_type_hint in (
                (snapshot.access_token, "access_token"),
                (snapshot.refresh_token, "refresh_token"),
            ):
                if token is None:
                    continue
                request_kwargs: dict[str, Any] = {
                    "data": {
                        **data_base,
                        "token": token,
                        "token_type_hint": token_type_hint,
                    },
                    "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                }
                if auth is not None:
                    request_kwargs["auth"] = auth
                try:
                    response = await oauth_post(
                        snapshot.revocation_endpoint,
                        client=http_client,
                        **request_kwargs,
                    )
                    if response.status_code >= 400:
                        logger.warning(
                            "Issued MCP OAuth token revocation returned HTTP %s "
                            "for flow %s",
                            response.status_code,
                            snapshot.flow_id,
                        )
                except Exception:
                    logger.warning(
                        "Issued MCP OAuth token revocation failed for flow %s",
                        snapshot.flow_id,
                    )
    except Exception:
        logger.warning(
            "Issued MCP OAuth token revocation could not start for flow %s",
            snapshot.flow_id,
        )


async def _rollback_and_revoke_mcp_oauth_issued_token(
    db: Session,
    snapshot: _MCPOAuthIssuedTokenSnapshot,
) -> None:
    """Release database locks before any compensating provider request."""
    db.rollback()
    await _revoke_mcp_oauth_issued_token_externally(snapshot)


@dataclass(frozen=True)
class _MCPOAuthGrantRevocationSnapshot:
    grant_id: int
    revocation_endpoint: str | None
    client_id: str
    encrypted_client_secret: str | None
    token_endpoint_auth_method: str
    encrypted_access_token: str | None
    encrypted_refresh_token: str | None


def _mcp_oauth_grant_revocation_snapshot(
    *, client: MCPOAuthClient, grant: MCPOAuthGrant
) -> _MCPOAuthGrantRevocationSnapshot:
    metadata: dict[str, Any] = (
        client.metadata_json if isinstance(client.metadata_json, dict) else {}
    )
    revocation_endpoint = metadata.get("revocation_endpoint")
    return _MCPOAuthGrantRevocationSnapshot(
        grant_id=int(grant.id),
        revocation_endpoint=(
            revocation_endpoint
            if isinstance(revocation_endpoint, str) and revocation_endpoint
            else None
        ),
        client_id=str(client.client_id),
        encrypted_client_secret=(
            str(client.client_secret) if client.client_secret else None
        ),
        token_endpoint_auth_method=str(client.token_endpoint_auth_method or "none"),
        encrypted_access_token=(
            str(grant.access_token) if grant.access_token else None
        ),
        encrypted_refresh_token=(
            str(grant.refresh_token) if grant.refresh_token else None
        ),
    )


async def _revoke_mcp_oauth_grant_snapshot_externally(
    snapshot: _MCPOAuthGrantRevocationSnapshot,
) -> None:
    """Best-effort revoke a detached grant snapshot without ORM access."""
    if snapshot.revocation_endpoint is None:
        return

    try:
        client_secret = (
            decrypt_value(snapshot.encrypted_client_secret)
            if snapshot.encrypted_client_secret
            else ""
        )
    except Exception as exc:
        logger.warning(
            "Skipping MCP OAuth token revocation for grant %s "
            "(stage=decrypt_client_secret, exception_type=%s)",
            snapshot.grant_id,
            type(exc).__name__,
        )
        return
    auth_method = snapshot.token_endpoint_auth_method
    auth: httpx.Auth | None = None
    base_data: dict[str, str] = {"client_id": snapshot.client_id}
    if auth_method == "client_secret_post" and client_secret:
        base_data["client_secret"] = client_secret
    elif auth_method == "client_secret_basic" and client_secret:
        auth = httpx.BasicAuth(snapshot.client_id, client_secret)
    elif auth_method not in {"none", "client_secret_post", "client_secret_basic"}:
        logger.warning(
            "Skipping MCP OAuth token revocation for grant %s "
            "(stage=validate_auth_method)",
            snapshot.grant_id,
        )
        return

    try:
        async with create_mcp_oauth_http_client(
            timeout=MCP_OAUTH_HTTP_TIMEOUT_SECONDS,
        ) as http_client:
            for encrypted_token, token_type_hint in (
                (snapshot.encrypted_access_token, "access_token"),
                (snapshot.encrypted_refresh_token, "refresh_token"),
            ):
                if not encrypted_token:
                    continue
                try:
                    decrypted_token = decrypt_value(encrypted_token)
                except Exception as exc:
                    logger.warning(
                        "Skipping MCP OAuth token revocation for grant %s "
                        "(stage=decrypt_%s, exception_type=%s)",
                        snapshot.grant_id,
                        token_type_hint,
                        type(exc).__name__,
                    )
                    continue
                request_kwargs: dict[str, Any] = {
                    "data": {
                        **base_data,
                        "token": decrypted_token,
                        "token_type_hint": token_type_hint,
                    },
                    "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                }
                if auth is not None:
                    request_kwargs["auth"] = auth
                try:
                    response = await oauth_post(
                        snapshot.revocation_endpoint,
                        client=http_client,
                        **request_kwargs,
                    )
                    if response.status_code >= 400:
                        logger.warning(
                            "MCP OAuth token revocation returned HTTP %s for grant %s",
                            response.status_code,
                            snapshot.grant_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "MCP OAuth token revocation failed for grant %s "
                        "(stage=revoke_%s, exception_type=%s)",
                        snapshot.grant_id,
                        token_type_hint,
                        type(exc).__name__,
                    )
    except Exception as exc:
        logger.warning(
            "MCP OAuth token revocation could not start for grant %s "
            "(stage=create_client, exception_type=%s)",
            snapshot.grant_id,
            type(exc).__name__,
        )


async def _revoke_mcp_oauth_grant_externally(
    *,
    client: MCPOAuthClient,
    grant: MCPOAuthGrant,
) -> None:
    await _revoke_mcp_oauth_grant_snapshot_externally(
        _mcp_oauth_grant_revocation_snapshot(client=client, grant=grant)
    )


@dataclass(frozen=True)
class MCPOAuthOwnerRevocation:
    """Provider work staged by an exact-owner local transaction."""

    grant_count: int
    _snapshots: tuple[_MCPOAuthGrantRevocationSnapshot, ...]

    async def revoke_tokens(self) -> None:
        """Revoke provider tokens only after the caller commits local state."""
        for snapshot in self._snapshots:
            await _revoke_mcp_oauth_grant_snapshot_externally(snapshot)


def _trusted_mcp_oauth_owner_key(resource_owner_key: str, *, user_id: int) -> str:
    try:
        owner_key = normalize_user_oauth_resource_owner_key(resource_owner_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if owner_key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="resource_owner_key must not be null",
        )
    if owner_key == _default_resource_owner_key(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="resource_owner_key must not use the default user namespace",
        )
    return owner_key


def _upsert_mcp_oauth_grant(
    db: Session,
    *,
    flow_state: MCPOAuthFlowState,
    token_data: dict[str, Any],
) -> MCPOAuthGrant:
    scope = _scope_string(str(token_data.get("scope") or flow_state.scope))
    lookup_hash = mcp_oauth_grant_lookup_hash(
        flow_state.mcp_server_id,
        flow_state.user_id,
        flow_state.resource_owner_key,
        flow_state.mcp_oauth_client_id,
        flow_state.issuer,
        flow_state.resource,
        scope,
    )
    existing = (
        db.query(MCPOAuthGrant)
        .filter(
            MCPOAuthGrant.lookup_hash == lookup_hash,
        )
        .first()
    )
    grant = existing or MCPOAuthGrant(
        mcp_server_id=flow_state.mcp_server_id,
        user_id=flow_state.user_id,
        mcp_oauth_client_id=flow_state.mcp_oauth_client_id,
        lookup_hash=lookup_hash,
        resource_owner_key=flow_state.resource_owner_key,
        issuer=flow_state.issuer,
        resource=flow_state.resource,
        scope=scope,
    )
    setattr(grant, "access_token", encrypt_value(str(token_data["access_token"])))
    refresh_token = token_data.get("refresh_token")
    if refresh_token:
        setattr(grant, "refresh_token", encrypt_value(str(refresh_token)))
    setattr(
        grant,
        "token_type",
        _bounded_mcp_oauth_value(
            str(token_data.get("token_type") or "Bearer"),
            field_name="token_type",
            max_length=MCP_OAUTH_TOKEN_TYPE_MAX_LENGTH,
        ),
    )
    setattr(grant, "status", "active")
    setattr(grant, "revoked_at", None)
    setattr(
        grant,
        "metadata_json",
        {
            key: value
            for key, value in token_data.items()
            if key not in {"access_token", "refresh_token"}
        },
    )
    setattr(grant, "expires_at", oauth_token_expires_at(token_data))
    if existing is None:
        db.add(grant)
    return grant


class MCPConfigFieldRegistry:
    """Registry of field parsers for different configuration fields."""

    # Field type mappings
    STRING_LIST_FIELDS = {"args", "volumes"}
    KEY_VALUE_DICT_FIELDS = {"env", "headers", "docker_environment"}
    PORT_MAPPING_FIELDS = {"bind_ports"}
    BOOLEAN_FIELDS = {"auto_start", "concurrency_safe"}
    JSON_FIELDS = {"headers"}  # Fields that should prefer JSON parsing

    @classmethod
    def get_parser_for_field(cls, field_name: str) -> Optional[Callable]:
        """Get the appropriate parser function for a field."""
        if field_name in cls.STRING_LIST_FIELDS:
            return ConfigFieldParser.parse_string_list
        elif field_name in cls.KEY_VALUE_DICT_FIELDS:
            return ConfigFieldParser.parse_key_value_dict
        elif field_name in cls.PORT_MAPPING_FIELDS:
            return ConfigFieldParser.parse_port_mappings
        elif field_name in cls.BOOLEAN_FIELDS:
            return ConfigFieldParser.parse_boolean
        return None


class TransportFieldValidator:
    """Validate fields based on transport type."""

    TRANSPORT_REQUIRED_FIELDS = {
        "stdio": {"command"},
        "sse": {"url"},
        "websocket": {"url"},
        "streamable_http": {"url"},
    }

    TRANSPORT_OPTIONAL_FIELDS = {
        "stdio": {"args", "env", "cwd"},
        "sse": {"headers"},
        "websocket": {"headers"},
        "streamable_http": {"headers"},
    }

    @classmethod
    def validate_transport_fields(
        cls, transport: str, config_dict: Dict[str, Any]
    ) -> None:
        """Validate that required fields are present for the transport type."""
        if transport not in cls.TRANSPORT_REQUIRED_FIELDS:
            raise ValueError(f"Unsupported MCP transport: {transport}")
        required_fields = cls.TRANSPORT_REQUIRED_FIELDS.get(transport, set())

        for field in required_fields:
            if field not in config_dict or config_dict[field] is None:
                raise ValueError(f"Transport '{transport}' requires field '{field}'")


def _build_server_config(
    server_data: MCPServerCreate, existing_server: Optional[MCPServer] = None
) -> MCPServerConfig:
    """Build MCPServerConfig from API request data using modular parsing."""
    # Start with base config
    config_dict = {
        "name": server_data.name,
        "transport": server_data.transport,
        "description": server_data.description,
        "managed": "external",  # Default for user-created servers
    }

    # Parse and add config fields
    if server_data.config:
        for field_name, value in server_data.config.items():
            if field_name not in [
                "name",
                "transport",
                "description",
            ]:  # Skip already handled fields
                try:
                    parsed_value = _parse_config_field(
                        field_name, value, server_data.transport
                    )

                    if parsed_value is not None:
                        config_dict[field_name] = parsed_value
                except ValueError as e:
                    raise ValueError(
                        f"Configuration error in field '{field_name}': {str(e)}"
                    )

    # For updates, preserve existing values if not provided
    if existing_server:
        existing_config = existing_server.to_config_dict()
        for key, value in existing_config.items():
            if key not in config_dict and value is not None:
                config_dict[key] = value

    TransportFieldValidator.validate_transport_fields(
        server_data.transport, config_dict
    )

    return MCPServerConfig(**config_dict)


def _validate_mcp_runtime_config(
    *,
    runtime_input_schema: Any,
    runtime_bindings: Any,
    allow_delegated_authorization: bool,
    static_headers: Any,
) -> None:
    headers = static_headers if isinstance(static_headers, dict) else None
    validate_runtime_config_declaration(
        connector_type="mcp",
        runtime_input_schema=runtime_input_schema,
        runtime_bindings=runtime_bindings,
        allow_delegated_authorization=allow_delegated_authorization,
        static_headers=headers,
    )


# Every MCPServer field a generic server update/create request can set.
# Config attributes and DB column names match; _update_server_from_config
# uses this list to preserve the shared configuration consistently.
_MCP_SERVER_CONFIGURABLE_FIELDS = (
    "name",
    "description",
    "transport",
    "managed",
    "command",
    "args",
    "url",
    "env",
    "cwd",
    "headers",
    "timeout",
    "auth",
    "concurrency_safe",
    "concurrent_tools",
    "docker_url",
    "docker_image",
    "docker_environment",
    "docker_working_dir",
    "volumes",
    "bind_ports",
    "restart_policy",
    "auto_start",
)


def _update_server_from_config(server: MCPServer, config: MCPServerConfig) -> None:
    """Update database server object from MCPServerConfig."""
    # Map config fields to database fields
    field_mapping = {field: field for field in _MCP_SERVER_CONFIGURABLE_FIELDS}

    for config_field, db_field in field_mapping.items():
        if hasattr(config, config_field) and hasattr(server, db_field):
            value = getattr(config, config_field)
            if config_field == "env" and value and isinstance(value, dict):
                from xagent.core.utils.encryption import encrypt_env_dict

                # Masked values ("********") mean "keep the stored secret".
                value = encrypt_env_dict(
                    _merge_masked_env(value, getattr(server, "env", None) or {})
                )
            elif config_field == "auth" and value and isinstance(value, dict):
                from xagent.core.utils.encryption import encrypt_value

                encrypted_auth = value.copy()
                for key in SENSITIVE_AUTH_FIELDS:
                    if key in encrypted_auth and encrypted_auth[key]:
                        # If masked, retain the existing encrypted value from the database
                        if encrypted_auth[key] == MASKED_SECRET_VALUE:
                            existing_auth: Any = server.auth or {}
                            encrypted_auth[key] = existing_auth.get(key)
                        else:
                            # encrypt_value is idempotent (skips already-encrypted)
                            encrypted_auth[key] = encrypt_value(encrypted_auth[key])
                value = encrypted_auth
            setattr(server, db_field, value)


def _parse_config_field(
    field_name: str, value: Any, transport: str | None = None
) -> Any:
    """
    Parse configuration field based on its expected type.

    Args:
        field_name: Name of the configuration field
        value: Raw value to parse
        transport: Transport type (for transport-specific parsing if needed)

    Returns:
        Parsed value in the appropriate type
    """
    # Handle None or empty values
    if value is None or value == "":
        return None

    # If not a string, return as-is (already parsed)
    if not isinstance(value, str):
        return value

    # Clean up string value
    value = value.strip()
    if not value:
        return None

    # Get parser for this field
    parser = MCPConfigFieldRegistry.get_parser_for_field(field_name)

    if parser:
        try:
            result = parser(value)
            # Return None for empty results
            if isinstance(result, (dict, list)) and not result:
                return None
            return result
        except Exception as e:
            raise ValueError(f"Failed to parse field '{field_name}': {str(e)}")

    # Default: return string value as-is
    return value


def _mask_env(env: Any) -> dict:
    """Mask env values for API responses (keys stay visible for editing)."""
    return {k: (MASKED_SECRET_VALUE if v else v) for k, v in env.items()}


def _merge_masked_env(new_env: dict, old_env: dict) -> dict:
    """Apply an incoming env dict, restoring the stored value for masked entries.

    The mask is a same-key retention token. Rejecting an unknown masked key
    prevents a rename from silently deleting the old credential while reporting
    a successful replacement.
    """
    merged = {}
    for k, v in new_env.items():
        if v == MASKED_SECRET_VALUE:
            if k in old_env and old_env[k] is not None:
                merged[k] = old_env[k]
            else:
                raise ValueError(
                    f"Masked secret '{k}' has no stored value; provide a new value"
                )
        else:
            merged[k] = v
    return merged


def _check_mcp_permission(
    user_mcp: "UserMCPServer | _TeamOwnedUserMCP",
    is_admin: bool,
    require: str = "edit",
) -> bool:
    """Whether the user may mutate shared MCP config.

    ``edit`` gates changes to the shared global config; ``delete`` gates
    removing the shared server. Admins bypass both.
    """
    if is_admin:
        return True
    is_owner = bool(getattr(user_mcp, "is_owner", False))
    if require == "delete":
        # The owner can always delete; can_delete additionally grants it to a
        # non-owner. Checking is_owner too covers rows created before can_delete
        # was set (e.g. OAuth provisioning, migration-skipped is_owner rows).
        return is_owner or bool(getattr(user_mcp, "can_delete", False))
    return is_owner


# Owner-only global fields that are safe to compare (non-secret; secret values
# like env/headers and auth's SENSITIVE_AUTH_FIELDS round-trip as masks and
# can't be diffed reliably, so they keep the silent-preserve behavior).
_GLOBAL_CONFIG_KEYS = ("command", "args", "url")


def _auth_metadata_tampered(incoming_auth: Any, current_auth: Any) -> bool:
    """True if a payload changes non-secret auth metadata (client_id, issuer …)."""
    if not isinstance(incoming_auth, dict):
        return False
    current = current_auth if isinstance(current_auth, dict) else {}
    return any(
        key not in SENSITIVE_AUTH_FIELDS and value != current.get(key)
        for key, value in incoming_auth.items()
    )


def _global_config_tampered(server_data: MCPServerUpdate, server: MCPServer) -> bool:
    """True if a payload changes owner-only global fields (non-secret ones)."""
    fields_set = server_data.model_fields_set
    if server_data.name is not None and server_data.name != server.name:
        return True
    if server_data.transport is not None and server_data.transport != server.transport:
        return True
    if (
        server_data.description is not None
        and server_data.description != server.description
    ):
        return True
    incoming = server_data.config or {}
    current = server.to_config_dict()
    if any(
        key in incoming and incoming[key] != current.get(key)
        for key in _GLOBAL_CONFIG_KEYS
    ):
        return True
    if (
        "runtime_input_schema" in fields_set
        and server_data.runtime_input_schema != server.runtime_input_schema
    ):
        return True
    if (
        "runtime_bindings" in fields_set
        and server_data.runtime_bindings != server.runtime_bindings
    ):
        return True
    if "allow_delegated_authorization" in fields_set and bool(
        server_data.allow_delegated_authorization
    ) != bool(server.allow_delegated_authorization):
        return True
    return _auth_metadata_tampered(incoming.get("auth"), current.get("auth"))


class _TeamOwnedUserMCP:
    """Stand-in for a missing UserMCPServer row: a team connector the user does
    not personally own. Exposes the attributes the response builders read with
    not-owned defaults (usable, but not editable/deletable).

    ``__slots__`` declares only ``user_id`` as a real per-instance attribute.
    Every other name below is a class attribute, not a slot, so assigning to
    it on an instance -- ``stand_in.is_active = False``, say -- raises
    ``AttributeError`` instead of silently creating a shadowing instance
    attribute the caller's own row never backs. A caller admitted through
    this stand-in has no association row to write, so nothing here should
    ever be writable.
    """

    __slots__ = ("user_id",)

    is_owner = False
    can_edit = False
    can_delete = False
    is_active = True
    is_default = False
    env = None
    env_source = None

    def __init__(self, user_id: int) -> None:
        self.user_id = int(user_id)


class _TeamOwnedUserApi:
    """Stand-in for a missing UserCustomApi row (team-owned, not user-owned).

    Same reasoning as ``_TeamOwnedUserMCP`` above: ``__slots__`` leaves
    ``user_id`` as the only attribute an instance can hold, so a write to
    ``can_edit``, ``is_active`` or ``is_default`` raises ``AttributeError``
    rather than shadowing the class default with a value nothing persists.
    """

    __slots__ = ("user_id",)

    can_edit = False
    is_active = True
    is_default = False

    def __init__(self, user_id: int) -> None:
        self.user_id = int(user_id)


def _db_server_to_response(
    server: MCPServer,
    user_mcp: UserMCPServer | _TeamOwnedUserMCP,
    manager: DatabaseMCPServerManager,
    is_admin: bool = False,
) -> MCPServerResponse:
    """Convert database MCPServer to response model."""
    # Get status from manager if available
    config = server.to_config_dict()

    # Mask sensitive auth fields for the frontend
    auth_config = config.get("auth")
    if auth_config and isinstance(auth_config, dict):
        masked_auth = auth_config.copy()
        for key in SENSITIVE_AUTH_FIELDS:
            if key in masked_auth and masked_auth[key]:
                masked_auth[key] = MASKED_SECRET_VALUE
        config["auth"] = masked_auth

    # Env values are secrets: mask them (keys stay visible so the UI can edit them).
    if isinstance(config.get("env"), dict):
        config["env"] = _mask_env(config["env"])

    return MCPServerResponse(
        id=server.id,
        user_id=user_mcp.user_id,
        name=server.name,
        transport=server.transport,
        description=server.description,
        config=config,
        is_active=user_mcp.is_active,
        is_default=user_mcp.is_default,
        user_env=_mask_env(getattr(user_mcp, "env", None)) if user_mcp.env else None,
        env_source=getattr(user_mcp, "env_source", None),
        runtime_input_schema=server.runtime_input_schema,
        runtime_bindings=server.runtime_bindings,
        allow_delegated_authorization=bool(server.allow_delegated_authorization),
        can_edit_global=_check_mcp_permission(user_mcp, is_admin, require="edit"),
        transport_display=server.transport_display,
        created_at=_format_optional_datetime(server.created_at),
        updated_at=_format_optional_datetime(server.updated_at),
    )


def _custom_api_to_mcp_response(
    api: CustomApi,
    user_api: UserCustomApi | _TeamOwnedUserApi,
) -> MCPServerResponse:
    """Project a Custom API into the aggregate connector response contract."""
    masked_env: dict[str, Any] = _mask_env(api.env) if isinstance(api.env, dict) else {}
    config: dict[str, Any] = {"env": masked_env}
    for field_name in ("url", "method", "headers", "body"):
        value = getattr(api, field_name)
        if value:
            config[field_name] = value

    return MCPServerResponse(
        id=api.id,
        user_id=user_api.user_id,
        name=api.name,
        transport="custom_api",
        description=api.description,
        config=config,
        is_active=user_api.is_active,
        is_default=user_api.is_default,
        user_env=None,
        runtime_input_schema=api.runtime_input_schema,
        runtime_bindings=api.runtime_bindings,
        allow_delegated_authorization=bool(api.allow_delegated_authorization),
        can_edit_global=bool(user_api.can_edit),
        transport_display="Custom API",
        created_at=_format_optional_datetime(api.created_at),
        updated_at=_format_optional_datetime(api.updated_at),
    )


def _append_team_mcp_responses(
    db: Session,
    manager: DatabaseMCPServerManager,
    responses: list[MCPServerResponse],
    *,
    user_id: int,
    is_admin: bool,
    user_mcps: list[Any],
    user_custom_apis: list[Any],
) -> None:
    """Include visible team connectors with no personal association."""
    from ..services.connector_team_scope import visible_team_connector_ids

    team_ids = visible_team_connector_ids(db, user_id)
    own_mcp_ids = {int(server.id) for _um, server in user_mcps}
    missing_mcp = [sid for sid in team_ids["mcp"] if sid not in own_mcp_ids]
    if missing_mcp:
        for server in db.query(MCPServer).filter(MCPServer.id.in_(missing_mcp)).all():
            if is_retired_catalog_server(db, server):
                continue
            responses.append(
                _db_server_to_response(
                    server, _TeamOwnedUserMCP(user_id), manager, is_admin=is_admin
                )
            )
    own_api_ids = {int(api.id) for _ua, api in user_custom_apis}
    missing_api = [aid for aid in team_ids["custom_api"] if aid not in own_api_ids]
    if missing_api:
        for api in db.query(CustomApi).filter(CustomApi.id.in_(missing_api)).all():
            responses.append(
                _custom_api_to_mcp_response(api, _TeamOwnedUserApi(user_id))
            )


@mcp_router.get("/servers", response_model=List[MCPServerResponse])
def get_mcp_servers(
    user_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[MCPServerResponse]:
    """List MCP servers for the current user (admins may pass user_id to inspect another user)."""
    try:
        manager = DatabaseMCPServerManager(db)
        if user_id is not None and user_id != current_user.id:
            if not is_admin_user(current_user):
                raise HTTPException(status_code=403, detail="Admin required")
            effective_user_id = int(user_id)
        else:
            effective_user_id = int(current_user.id)

        # Get user's MCP servers
        user_mcps = (
            db.query(UserMCPServer, MCPServer)
            .join(MCPServer, UserMCPServer.mcpserver_id == MCPServer.id)
            .filter(UserMCPServer.user_id == effective_user_id)
            .order_by(MCPServer.created_at.desc())
            .all()
        )

        is_admin = getattr(current_user, "is_admin", False)
        responses = []
        for user_mcp, server in user_mcps:
            if is_retired_catalog_server(db, server):
                continue
            responses.append(
                _db_server_to_response(
                    server,
                    user_mcp,
                    manager,
                    is_admin=is_admin,
                )
            )

        # Append Custom APIs
        user_custom_apis = (
            db.query(UserCustomApi, CustomApi)
            .join(CustomApi, UserCustomApi.custom_api_id == CustomApi.id)
            .filter(UserCustomApi.user_id == effective_user_id)
            .all()
        )

        for user_api, api in user_custom_apis:
            responses.append(_custom_api_to_mcp_response(api, user_api))

        _append_team_mcp_responses(
            db,
            manager,
            responses,
            user_id=effective_user_id,
            is_admin=is_admin,
            user_mcps=user_mcps,
            user_custom_apis=user_custom_apis,
        )

        return responses

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list MCP servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list MCP servers",
        )


@mcp_router.get("/servers/{server_id}", response_model=MCPServerResponse)
def get_mcp_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPServerResponse:
    """Get a specific MCP server."""
    try:
        manager = DatabaseMCPServerManager(db)
        user_id = cast(int, current_user.id)

        user_mcp, server = _get_user_mcp_server_or_404(
            db, user_id=user_id, server_id=server_id
        )

        return _db_server_to_response(
            server,
            user_mcp,
            manager,
            is_admin=getattr(current_user, "is_admin", False),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get MCP server",
        )


@mcp_router.post(
    "/servers", response_model=MCPServerResponse, status_code=status.HTTP_201_CREATED
)
def create_mcp_server(
    server_data: MCPServerCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPServerResponse:
    """Create a new MCP server."""
    try:
        manager = DatabaseMCPServerManager(db)
        user_id = current_user.id

        # Validate per-user masks before manager.add_server can persist the
        # global row. New connectors have no stored value a mask could retain.
        try:
            created_user_env = (
                _merge_masked_env(server_data.user_env, {})
                if server_data.user_env
                else None
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid user environment variables: {exc}",
            ) from exc

        # Check if server name already exists
        existing = (
            db.query(MCPServer).filter(MCPServer.name == server_data.name).first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MCP server '{server_data.name}' already exists",
            )

        # Build and validate config
        try:
            config = _build_server_config(server_data)
            _validate_mcp_runtime_config(
                runtime_input_schema=server_data.runtime_input_schema,
                runtime_bindings=server_data.runtime_bindings,
                allow_delegated_authorization=server_data.allow_delegated_authorization,
                static_headers=config.headers,
            )
            if isinstance(config.env, dict):
                _merge_masked_env(config.env, {})
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid configuration: {str(e)}",
            )

        # Add server using manager
        manager.add_server(config)

        # Get the created server
        server = db.query(MCPServer).filter(MCPServer.name == server_data.name).first()
        if not server:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create server",
            )
        orm_server = cast(Any, server)
        orm_server.runtime_input_schema = server_data.runtime_input_schema
        orm_server.runtime_bindings = server_data.runtime_bindings
        orm_server.allow_delegated_authorization = (
            server_data.allow_delegated_authorization
        )

        # Create user-server association. The creator owns the global config.
        from xagent.core.utils.encryption import encrypt_env_dict

        encrypted_user_env = None
        if created_user_env:
            # No stored values yet: drop masked entries, then encrypt at rest.
            encrypted_user_env = encrypt_env_dict(created_user_env) or None
        user_mcp = UserMCPServer(
            user_id=user_id,
            mcpserver_id=server.id,
            is_active=server_data.is_active,
            is_owner=True,
            can_edit=True,
            can_delete=True,
            env=encrypted_user_env,
        )
        db.add(user_mcp)

        db.commit()
        db.refresh(user_mcp)

        logger.info(f"Created MCP server '{server_data.name}' for user {user_id}")
        return _db_server_to_response(
            server, user_mcp, manager, is_admin=getattr(current_user, "is_admin", False)
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create MCP server: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create MCP server: {str(e)}",
        )


@mcp_router.put("/servers/{server_id}", response_model=MCPServerResponse)
def update_mcp_server(
    server_id: int,
    server_data: MCPServerUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPServerResponse:
    """Update an existing MCP server."""
    try:
        manager = DatabaseMCPServerManager(db)
        user_id = cast(int, current_user.id)

        user_mcp, server = _get_user_mcp_server_or_404(
            db, user_id=user_id, server_id=server_id
        )
        can_edit_global = _check_mcp_permission(
            user_mcp, getattr(current_user, "is_admin", False), require="edit"
        )

        # Which row a request writes decides which row it locks. Seven of the
        # nine fields of ``MCPServerUpdate`` target the shared ``MCPServer``
        # definition row; ``is_active`` and ``user_env`` write this caller's
        # own ``UserMCPServer`` link row and nothing else. Locking the
        # definition row for a payload that carries only those two made an
        # activate/deactivate queue behind an unrelated edit of the same
        # connector -- and, under PostgreSQL REPEATABLE READ, made it fail
        # outright: the lock statement follows the snapshot this session's
        # first read in the request already established. In a real request
        # that first read is ``get_current_user``'s own lookup of the caller
        # (``_resolve_access_token_user``, ``auth_dependencies.py``), which
        # FastAPI's dependency injection runs on this same session before
        # this route body executes at all -- the join immediately below is
        # not what pins the snapshot in production. It is what pins it in
        # the PostgreSQL lock suite's tests, though: those call this
        # function directly and skip the dependency chain, so for them the
        # join below is the session's first statement. Either way, a
        # definition edit committed in between raises SQLSTATE 40001 and the
        # route's generic handler answers 500 with the requested activation
        # state unwritten.
        #
        # ``model_fields_set`` decides this, not the values, because that is
        # what decides the writes themselves: an explicitly-null
        # ``runtime_input_schema`` is written to the definition row below even
        # though its value is ``None``, while an absent field is not written at
        # all. So a payload carrying ``description=None`` is counted here and
        # then skipped by the write at its own ``is not None`` guard -- the
        # lock is taken in a few cases that did not need it, and skipped in
        # none whose fields target that row.
        #
        # One flag, three decisions, and they are the same decision on
        # purpose: whether to take the row lock (the ``with_for_update``
        # below), whether to re-derive the caller's write authority after
        # that wait (the block right after the lock), and whether to
        # rebuild, validate and write the definition row (the block
        # further down). Moving a field into the exclusion set above
        # therefore also drops that payload's post-lock re-authorization --
        # the hazard ``custom_api.py``'s equivalent comment warns about --
        # and drops its runtime-config validation with it. Change that set
        # only with all three in view;
        # ``tests/web/api/test_mcp_update_lock_partition.py`` fails on a
        # field added to ``MCPServerUpdate`` without that decision.
        #
        # This set is also exactly the set of payloads the block below
        # rebuilds and writes the definition row for: the whole rebuild --
        # building ``update_data``, validating it, and writing every
        # resulting field back onto ``server`` -- runs only when
        # ``writes_definition_row`` is true. A lock-free payload therefore
        # cannot produce a definition-row write: there is no code path left
        # that would build one. (An earlier version of this route rebuilt
        # the config unconditionally regardless of which row the lock
        # covered, so a server carrying a global ``env`` or ``auth`` still
        # got its value re-encrypted and written back with no lock held --
        # this is why the two now share one flag instead of the write
        # having its own, independently-derived guard.)
        fields_set = server_data.model_fields_set
        writes_definition_row = bool(fields_set - {"is_active", "user_env"})

        # A fresh single-table read of the definition row, on both paths. The
        # read above is a two-table join and cannot itself address just this
        # table; this is a separate statement, so a row deleted between the two
        # still yields None here (handled as the same 404) rather than
        # surfacing as an unrelated error out of the write path below.
        # ``populate_existing()`` makes this statement's row the one the rest
        # of this route reads: without it the already-identity-mapped instance
        # from the join above would be returned unrefreshed, and every field
        # below would still be that earlier snapshot.
        #
        # ``FOR UPDATE`` is added only on the path that writes this row, so a
        # request that writes it still waits for another request holding it.
        # That clause is a PostgreSQL/MySQL row lock only: SQLAlchemy renders
        # no locking clause at all on SQLite, so on a SQLite deployment the
        # read-modify-write below is not serialized and two concurrent edits of
        # one server can still interleave. Closing that window needs the
        # dual-dialect fence ``acquire_runtime_key_transition_fence``
        # (services/api_keys.py) uses -- a no-op ``UPDATE`` that takes SQLite's
        # writer lock -- and is left to a change of its own.
        #
        # ``key_share=True`` asks PostgreSQL for ``FOR NO KEY UPDATE`` instead
        # of plain ``FOR UPDATE``: this route never changes ``MCPServer.id``
        # (the column any referencing foreign key cares about), only the
        # other columns, so the weaker lock covers everything it writes.
        # Plain ``FOR UPDATE`` would also block a concurrent connect or
        # disconnect on the same server -- ``UserMCPServer`` inserts and
        # deletes take a ``FOR KEY SHARE`` lock on the ``MCPServer`` row they
        # reference, to verify that row still exists -- and ``FOR KEY SHARE``
        # is compatible with ``FOR NO KEY UPDATE`` but not with plain
        # ``FOR UPDATE``. On MySQL, which has no such distinction,
        # ``key_share`` is accepted and ignored: SQLAlchemy renders plain
        # ``FOR UPDATE`` there either way.
        #
        # The weaker mode is not what leaves the residual window the
        # re-reads after the lock cover: neither deleting a
        # ``UserMCPServer`` row nor clearing a ``User.is_admin`` flag takes
        # any lock on this row in either mode, so plain ``FOR UPDATE``
        # would leave exactly the same window open. See the re-read block
        # below for how it is handled instead.
        definition_query = (
            db.query(MCPServer).filter(MCPServer.id == server_id).populate_existing()
        )
        if writes_definition_row:
            definition_query = definition_query.with_for_update(key_share=True)
        current_server = definition_query.first()
        if current_server is None:
            # This statement holds a row lock on ``writes_definition_row``
            # (see the comment on the query above), so it opened a
            # transaction the outer ``except HTTPException: raise`` will not
            # close: that handler re-raises without rolling back, unlike its
            # ``except Exception`` sibling further down. Roll back explicitly
            # -- matching the same-shaped 404 below and the two later
            # branches that raise after a write -- so this 404 does not
            # leave a transaction (and any lock it took) open.
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found"
            )
        server = current_server

        if writes_definition_row:
            # The join gate above ran before the lock statement, and the
            # lock statement waits. Both inputs to ``can_edit_global`` --
            # that the caller still has a ``UserMCPServer`` link to this
            # server (link ownership), and whether the caller is a platform
            # admin -- were read from that pre-wait state: the gate's join
            # for the link, ``current_user`` (built once by the auth
            # dependency before this route even started) for admin status.
            # A supported admin user deletion removes association rows and
            # leaves every definition row standing; a platform admin's own
            # admin flag can itself be revoked by another admin; an MCP
            # disconnect removes the caller's own link while another user's
            # link keeps the definition alive. Any of these can commit
            # inside the wait. The request would then write and commit the
            # shared definition row on a revoked authority, and fail only
            # afterwards, in ``db.refresh(user_mcp)`` below -- which runs
            # after the commit, so the generic handler's rollback cannot
            # take the shared write back and the caller sees a 500 over a
            # durable change.
            #
            # ``populate_existing()`` on the definition query above
            # refreshes that statement's row and nothing else, so both
            # inputs need their own fresh single-table reads here -- the
            # gate above is a two-table join and cannot address either
            # table alone. The link read below replaces ``user_mcp``; the
            # admin read further below replaces the value passed into
            # ``_check_mcp_permission``. Together they re-derive
            # ``can_edit_global`` for the rest of the route: the per-user
            # env write, the activation write, the refresh and the response
            # all read ``user_mcp``, and the owner-only guard below reads
            # ``can_edit_global``.
            #
            # A gone link is this route's existing 404, matching the gate.
            # A link that is still there but no longer owns the server is
            # not an error by itself here -- the gate does not refuse a
            # non-owner either -- so it is answered exactly as the gate
            # would have answered it: the owner-only guard below rejects a
            # payload that changes the shared configuration and drops one
            # that does not.
            current_user_mcp = (
                db.query(UserMCPServer)
                .filter(
                    UserMCPServer.user_id == user_id,
                    UserMCPServer.mcpserver_id == server_id,
                )
                .populate_existing()
                .first()
            )
            if current_user_mcp is None:
                # Same reasoning as the definition-row 404 above: this read
                # ran inside the transaction that held the lock, so an
                # explicit rollback keeps this 404 from leaving that
                # transaction open for the outer ``except HTTPException``
                # handler to skip past.
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="MCP server not found",
                )
            user_mcp = current_user_mcp
            # ``current_user.is_admin`` is the value the auth dependency's
            # own read fixed before this route's wait for the lock, same as
            # ``user_mcp``'s pre-lock read above -- and admin status is
            # exactly as revocable during that wait as link ownership is
            # (a supported admin action can strip it from another user
            # mid-request). The link re-read above already guards its half
            # of this permission check; this is the other half, re-read the
            # same way: a fresh single-table lookup of the caller's own row,
            # not the pre-wait object.
            current_admin_user = (
                db.query(User).filter(User.id == user_id).populate_existing().first()
            )
            if current_admin_user is None:
                # Distinct wording from the two 404s above on purpose: the
                # definition row and the caller's link to it were both still
                # there when this branch is reached, and what vanished inside
                # the lock wait is the caller's own account. Answering "MCP
                # server not found" here would name the wrong missing object.
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Requesting user account no longer exists",
                )
            can_edit_global = _check_mcp_permission(
                user_mcp, bool(current_admin_user.is_admin), require="edit"
            )

        # Read from the fresh definition-row read above, not the pre-lock
        # read further up: rename_team_connector's "old" argument must be
        # the name that read actually returned. On the path that writes
        # this row, that read is also the locked one, so this is the name
        # this transaction holds locked -- a concurrent committed rename in
        # between would otherwise make this stale, and the rewrite below
        # would then look for a name that no longer exists anywhere,
        # leaving the previous renamer's selectors dangling with no error.
        # On the lock-free path this concern does not arise: a payload that
        # skips the lock never carries ``name`` in ``fields_set``, so
        # ``old_name`` and the name written back below are always the same
        # string, and the rename hook below never fires for it.
        old_name = str(server.name)

        # Non-owners may not touch the shared global config (env, command, etc.);
        # they only get to set their own per-user env override below. Reject a
        # tampered payload outright (defense-in-depth for direct/stale-UI calls)
        # rather than silently normalizing it back to a 200.
        incoming_config = dict(server_data.config or {})
        if not can_edit_global:
            if _global_config_tampered(server_data, server):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only the server owner can change the shared configuration",
                )
            incoming_config = {}

        # Check for name conflicts if updating name
        if can_edit_global and server_data.name and server_data.name != server.name:
            existing = (
                db.query(MCPServer)
                .filter(MCPServer.name == server_data.name, MCPServer.id != server_id)
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"MCP server '{server_data.name}' already exists",
                )

        if writes_definition_row:
            # Build update config - only include provided fields. Non-owners keep the
            # existing global config untouched.
            update_data = MCPServerCreate(
                name=(server_data.name if can_edit_global else None) or server.name,
                transport=(server_data.transport if can_edit_global else None)
                or server.transport,
                description=server_data.description
                if can_edit_global and server_data.description is not None
                else server.description,
                config=incoming_config,
                is_active=server_data.is_active
                if server_data.is_active is not None
                else user_mcp.is_active,
            )

            # Build and validate config
            try:
                config = _build_server_config(update_data, server)
                runtime_input_schema = (
                    server_data.runtime_input_schema
                    if can_edit_global and "runtime_input_schema" in fields_set
                    else server.runtime_input_schema
                )
                runtime_bindings = (
                    server_data.runtime_bindings
                    if can_edit_global and "runtime_bindings" in fields_set
                    else server.runtime_bindings
                )
                allow_delegated_authorization = (
                    bool(server_data.allow_delegated_authorization)
                    if can_edit_global and "allow_delegated_authorization" in fields_set
                    else bool(server.allow_delegated_authorization)
                )
                _validate_mcp_runtime_config(
                    runtime_input_schema=runtime_input_schema,
                    runtime_bindings=runtime_bindings,
                    allow_delegated_authorization=allow_delegated_authorization,
                    static_headers=config.headers,
                )
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid configuration: {str(e)}",
                )

            # Update server fields (global config; no-op values for non-owners)
            try:
                _update_server_from_config(server, config)
            except ValueError as exc:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid environment variables: {exc}",
                ) from exc
            if can_edit_global:
                orm_server = cast(Any, server)
                if "runtime_input_schema" in fields_set:
                    orm_server.runtime_input_schema = runtime_input_schema
                if "runtime_bindings" in fields_set:
                    orm_server.runtime_bindings = runtime_bindings
                if "allow_delegated_authorization" in fields_set:
                    orm_server.allow_delegated_authorization = (
                        allow_delegated_authorization
                    )

        # Store this user's per-user env override (masked values keep stored secrets)
        if server_data.user_env is not None:
            from xagent.core.utils.encryption import encrypt_env_dict

            try:
                merged_user_env = _merge_masked_env(
                    server_data.user_env, getattr(user_mcp, "env", None) or {}
                )
            except ValueError as exc:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid user environment variables: {exc}",
                ) from exc
            setattr(user_mcp, "env", encrypt_env_dict(merged_user_env) or None)

        # Update user association if needed
        if server_data.is_active is not None:
            setattr(user_mcp, "is_active", server_data.is_active)

        from ..services.connector_team_scope import rename_team_connector

        rename_team_connector(
            db,
            int(user_id),
            "mcp",
            int(server_id),
            old_name,
            str(server.name),
            # This transaction holds the definition row FOR UPDATE ... KEY
            # SHARE and has not committed anything yet; a hook that ends it
            # drops that lock with this route none the wiser.
            caller_holds_lock=True,
        )

        db.commit()
        db.refresh(server)
        db.refresh(user_mcp)

        logger.info(f"Updated MCP server '{server.name}' for user {user_id}")
        return _db_server_to_response(
            server, user_mcp, manager, is_admin=getattr(current_user, "is_admin", False)
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update MCP server: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update MCP server: {str(e)}",
        )


@mcp_router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete an MCP server."""
    try:
        user_id = current_user.id

        # Check user has access to this server
        result = (
            db.query(UserMCPServer, MCPServer)
            .join(MCPServer, UserMCPServer.mcpserver_id == MCPServer.id)
            .filter(UserMCPServer.user_id == user_id, MCPServer.id == server_id)
            .first()
        )

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found"
            )

        user_mcp, server = result
        association_identity = _MCPOAuthAssociationIdentity(
            server_id=int(server.id),
            user_id=int(user_id),
            lifecycle_generation=user_mcp.lifecycle_generation,
        )
        # Serialize teardown with OAuth producers before enumerating artifacts.
        # The exact generation prevents a stale DELETE from touching a replacement
        # association created for the same user and shared server.
        lifecycle = _lock_active_mcp_oauth_lifecycle(
            db,
            association_identity=association_identity,
            association_must_be_active=False,
        )
        if lifecycle is None:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found"
            )
        server, user_mcp, _ = lifecycle

        # Deleting cascades to the shared config once no associations remain;
        # gate it on ownership, consistent with the update handler.
        if not _check_mcp_permission(
            user_mcp, getattr(current_user, "is_admin", False), require="delete"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to delete this MCP server",
            )

        if _has_actor_owned_mcp_oauth_state(
            db,
            server_id=server_id,
            user_id=int(user_id),
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="MCP server has actor-owned OAuth connections",
            )

        server_name = str(server.name)

        from ..services.connector_team_scope import delete_team_connector

        team_delete = delete_team_connector(
            db,
            int(user_id),
            "mcp",
            int(server_id),
            # Two row locks are already held here: _lock_active_mcp_oauth_lifecycle
            # above takes them on ``mcp_servers`` and ``user_mcpservers`` before
            # this call, and the same delete work follows as on the two locking
            # delete call sites. Declaring keeps the three of them answering the
            # same way.
            caller_holds_lock=True,
        )
        if team_delete.blocked_reason:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=team_delete.blocked_reason,
            )
        if team_delete.team_owned and not team_delete.authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a team admin can delete a team MCP server",
            )

        # Snapshot and purge this user's MCP OAuth grants for the server. On a
        # shared (multi-user) row the server outlives this disconnect, so
        # without this the grant's refresh token would stay usable — and its
        # row would stay stored — until the LAST user disconnects and the
        # cascade finally removes it. The provider-facing revocation happens
        # only after the local commit releases lifecycle locks. Use a hard
        # delete rather than a "revoked" status flip: a revoked grant
        # row is otherwise never swept, accumulating indefinitely as an
        # inert but secret-bearing row (F10).
        grant_revocations: list[_MCPOAuthGrantRevocationSnapshot] = []
        for grant in (
            db.query(MCPOAuthGrant)
            .filter(
                MCPOAuthGrant.mcp_server_id == server_id,
                MCPOAuthGrant.user_id == user_id,
                MCPOAuthGrant.status == "active",
            )
            .all()
        ):
            if isinstance(grant.oauth_client, MCPOAuthClient):
                grant_revocations.append(
                    _mcp_oauth_grant_revocation_snapshot(
                        client=grant.oauth_client, grant=grant
                    )
                )
            db.delete(grant)

        # This user's own flow-state rows (code_verifier etc.) are per-user,
        # unlike MCPOAuthClient which a shared server's other users may still
        # reference — safe to purge outright rather than only on server
        # deletion's cascade.
        db.query(MCPOAuthFlowState).filter(
            MCPOAuthFlowState.mcp_server_id == server_id,
            MCPOAuthFlowState.user_id == user_id,
        ).delete(synchronize_session=False)

        # Remove user-server association
        db.delete(user_mcp)
        db.flush()

        # Decide shared-row retention while the server lifecycle lock is still
        # held. A post-commit check followed by a second delete transaction can
        # erase a replacement association committed between those operations.
        other_users = (
            db.query(UserMCPServer)
            .filter(UserMCPServer.mcpserver_id == server_id)
            .first()
        )
        server_deleted = False
        retained_team_server = False
        if not other_users:
            retained_team_server = bool(
                team_delete.team_owned and not team_delete.delete_definition
            )
            if not retained_team_server:
                db.delete(server)
                server_deleted = True

        db.commit()

        for snapshot in grant_revocations:
            await _revoke_mcp_oauth_grant_snapshot_externally(snapshot)

        if retained_team_server:
            logger.info(f"Kept shared MCP server '{server_name}' after team disconnect")
        elif server_deleted:
            logger.info(f"Deleted MCP server '{server_name}'")
        else:
            logger.info(f"Removed user {user_id} access to MCP server '{server_name}'")

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to delete MCP server (exception_type=%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete MCP server",
        )


@mcp_router.post("/servers/{server_id}/toggle", response_model=MCPServerResponse)
async def toggle_mcp_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPServerResponse:
    """Toggle MCP server active status."""
    try:
        manager = DatabaseMCPServerManager(db)
        user_id = cast(int, current_user.id)

        user_mcp, server = _get_user_mcp_server_or_404(
            db, user_id=user_id, server_id=server_id
        )

        # Toggle active status
        setattr(user_mcp, "is_active", not user_mcp.is_active)
        db.commit()
        db.refresh(user_mcp)

        status_text = "activated" if user_mcp.is_active else "deactivated"
        logger.info(
            f"{status_text.capitalize()} MCP server '{server.name}' for user {user_id}"
        )

        return _db_server_to_response(
            server, user_mcp, manager, is_admin=getattr(current_user, "is_admin", False)
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to toggle MCP server: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to toggle MCP server",
        )


@mcp_router.get("/servers/{server_id}/logs")
async def get_mcp_server_logs(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    lines: int = 100,
) -> Dict[str, Any]:
    """Get logs for an internal MCP server."""
    try:
        manager = DatabaseMCPServerManager(db)
        user_id = cast(int, current_user.id)

        if not (1 <= lines <= 1000):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="lines must be between 1 and 1000",
            )

        _, server = _get_user_mcp_server_or_404(
            db, user_id=user_id, server_id=server_id
        )

        if server.managed != "internal":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Logs only available for internal servers",
            )

        log_lines = manager.get_logs(cast(str, server.name), lines)
        return {"server_name": server.name, "logs": log_lines or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get MCP server logs",
        )


@mcp_router.post("/test-connection", response_model=MCPConnectionTestResponse)
async def test_mcp_connection(
    test_data: MCPConnectionTest, db: Session = Depends(get_db)
) -> MCPConnectionTestResponse:
    """Test MCP server connection without saving."""
    if test_data.transport not in TransportFieldValidator.TRANSPORT_REQUIRED_FIELDS:
        return MCPConnectionTestResponse(
            success=False,
            message="Unsupported MCP transport.",
        )
    try:
        from ...core.tools.adapters.vibe.mcp_adapter import (
            load_mcp_tools_as_agent_tools,
        )

        connection: dict[str, Any] = {
            "name": test_data.name,
            "transport": test_data.transport,
        }

        connection.update(**test_data.config)

        try:
            # No ``connector_refs``: this connection is not persisted yet, so
            # there is no connector identity to carry. The tools built here
            # are only projected to names/descriptions and never dispatched,
            # so the approval gate is never consulted for them.
            connections_dict: Dict[str, Any] = {"test": connection}
            load_result = await load_mcp_tools_as_agent_tools(
                connections_dict, name_prefix="test_"
            )

            projection = _project_mcp_tool_load_result(load_result)
            details: dict[str, Any] = {"tool_count": len(projection.tools)}
            if projection.failures:
                details["failures"] = list(projection.failures)
            if projection.tools:
                return MCPConnectionTestResponse(
                    success=True,
                    message=f"Successfully connected to {test_data.name}. Loaded {len(projection.tools)} tools.",
                    details=details,
                )
            return MCPConnectionTestResponse(
                success=False,
                message=projection.failure_message,
                details=details,
            )

        except Exception as conn_error:
            logger.warning(
                "MCP connection test failed for '%s' (%s)",
                test_data.name,
                type(conn_error).__name__,
            )
            return MCPConnectionTestResponse(
                success=False,
                message=f"Failed to connect to {test_data.name}.",
                details={"error": "mcp_connection_test_failed"},
            )

    except Exception as e:
        logger.error("Failed to test MCP connection (%s)", type(e).__name__)
        return MCPConnectionTestResponse(
            success=False,
            message="Connection test failed.",
            details={"error": "mcp_connection_test_failed"},
        )


@mcp_router.get("/transports")
def get_supported_transports() -> dict:
    """Get list of supported transport types with descriptions."""
    return {
        "transports": [
            {
                "id": "stdio",
                "name": "STDIO",
                "description": "Standard input/output transport for local processes",
                "config_fields": [
                    {
                        "name": "command",
                        "type": "string",
                        "required": True,
                        "description": "Command to execute",
                    },
                    {
                        "name": "args",
                        "type": "array",
                        "required": False,
                        "description": "Command arguments",
                    },
                    {
                        "name": "env",
                        "type": "object",
                        "required": False,
                        "description": "Environment variables",
                    },
                    {
                        "name": "cwd",
                        "type": "string",
                        "required": False,
                        "description": "Working directory",
                    },
                ],
            },
            {
                "id": "sse",
                "name": "Server-Sent Events",
                "description": "HTTP-based transport using Server-Sent Events",
                "config_fields": [
                    {
                        "name": "url",
                        "type": "string",
                        "required": True,
                        "description": "Server URL",
                    },
                    {
                        "name": "headers",
                        "type": "object",
                        "required": False,
                        "description": "HTTP headers",
                    },
                ],
            },
            {
                "id": "websocket",
                "name": "WebSocket",
                "description": "WebSocket-based transport for real-time communication",
                "config_fields": [
                    {
                        "name": "url",
                        "type": "string",
                        "required": True,
                        "description": "WebSocket URL",
                    },
                    {
                        "name": "headers",
                        "type": "object",
                        "required": False,
                        "description": "WebSocket headers",
                    },
                ],
            },
            {
                "id": "streamable_http",
                "name": "Streamable HTTP",
                "description": "HTTP transport with streaming capabilities",
                "config_fields": [
                    {
                        "name": "url",
                        "type": "string",
                        "required": True,
                        "description": "Server URL",
                    },
                    {
                        "name": "headers",
                        "type": "object",
                        "required": False,
                        "description": "HTTP headers",
                    },
                ],
            },
        ]
    }


@mcp_router.get("/servers/{server_id}/tools")
async def get_mcp_server_tools(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Get tools available from a specific MCP server."""
    try:
        user_id = int(current_user.id)

        _, server = _get_user_mcp_server_or_404(
            db, user_id=user_id, server_id=server_id, require_active=True
        )

        from ..services.mcp_runtime import build_mcp_runtime_connection

        runtime_build = await build_mcp_runtime_connection(
            db,
            server,
            user_id=user_id,
        )
        if runtime_build.connection is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=runtime_build.diagnostic
                or {
                    "code": "authorization_required",
                    "message": "MCP server authorization is required",
                },
            )
        connection = runtime_build.connection

        # Try to load tools
        from ...core.tools.adapters.vibe.connector_runtime import ConnectorRef
        from ...core.tools.adapters.vibe.mcp_adapter import (
            load_mcp_tools_as_agent_tools,
        )

        server_name = server.name
        tools: List[Any] = []
        load_failures: tuple[dict[str, Any], ...] = ()
        if isinstance(server_name, str):
            connections_dict: Dict[str, Any] = {server_name: connection}
            # This listing never dispatches a call, but it is still a place
            # where MCP tools are materialized: carry the persisted server id
            # so every materialization seam in the process is identifiable,
            # rather than leaving one that would fail closed if it ever grew
            # an execution path.
            load_result = await load_mcp_tools_as_agent_tools(
                connections_dict,
                connector_refs={server_name: ConnectorRef("mcp", int(server_id))},
                name_prefix=f"server_{server_id}_",
            )
            projection = _project_mcp_tool_load_result(load_result)
            if not projection.tools:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "code": "mcp_tools_unavailable",
                        "message": projection.failure_message,
                        "failures": list(projection.failures),
                    },
                )
            tools = list(projection.tools)
            load_failures = projection.failures

        response = {
            "server_name": server.name,
            "tool_count": len(tools),
            "tools": [
                {
                    "name": getattr(tool, "name", str(tool)),
                    "description": getattr(
                        tool, "description", "No description available"
                    ),
                }
                for tool in tools
            ],
        }
        if load_failures:
            response["failures"] = list(load_failures)
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get MCP server tools (%s)", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get MCP server tools",
        )


@mcp_router.get("/oauth/callback")
async def mcp_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Complete MCP OAuth Authorization Code + PKCE and store an encrypted grant."""
    code = request.query_params.get("code")
    state_value = request.query_params.get("state")
    error = request.query_params.get("error")
    if not state_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_state", "message": "Missing OAuth state"},
        )
    flow_state = (
        db.query(MCPOAuthFlowState)
        .filter(MCPOAuthFlowState.state == state_value)
        .first()
    )
    if not flow_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_state", "message": "Invalid OAuth state"},
        )
    callback_redirect_after = (
        str(flow_state.redirect_after) if flow_state.redirect_after else None
    )
    try:
        _validate_mcp_oauth_state_cookie(request, state_value)
    except HTTPException as exc:
        cookie_detail: dict[str, Any] = (
            exc.detail if isinstance(exc.detail, dict) else {}
        )
        return _mcp_oauth_callback_error_redirect(
            flow_state,
            error_code=str(cookie_detail.get("code") or "invalid_state"),
            message=str(
                cookie_detail.get("message")
                or "OAuth callback state did not match this browser session"
            ),
        )
    state_error = _mcp_oauth_flow_state_error(db, flow_state)
    if state_error is not None:
        error_code, message = state_error
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code=error_code,
            message=message,
        )

    client = (
        db.query(MCPOAuthClient)
        .filter(MCPOAuthClient.id == flow_state.mcp_oauth_client_id)
        .first()
    )
    if not client:
        return _mcp_oauth_callback_error_redirect(
            flow_state,
            error_code="token_exchange_failed",
            message="OAuth client metadata not found",
        )

    try:
        flow_identity = _mcp_oauth_flow_identity(flow_state)
    except RuntimeError:
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code="invalid_state",
            message="OAuth state is not bound to an MCP server connection lifecycle",
        )
    association_identity = _MCPOAuthAssociationIdentity(
        server_id=flow_identity.server_id,
        user_id=flow_identity.user_id,
        lifecycle_generation=flow_identity.association_lifecycle_generation,
    )

    try:
        _validate_mcp_oauth_callback_issuer(
            request=request,
            client=client,
            flow_state=flow_state,
        )
    except HTTPException as exc:
        issuer_detail: dict[str, Any] = (
            exc.detail if isinstance(exc.detail, dict) else {}
        )
        return _mcp_oauth_callback_error_redirect(
            flow_state,
            error_code=str(issuer_detail.get("code") or "issuer_mismatch"),
            message=str(
                issuer_detail.get("message")
                or "Authorization response issuer did not match flow state"
            ),
        )
    code_verifier = decrypt_value(str(flow_state.code_verifier))
    resource = str(flow_state.resource)
    # The claim commit expires ORM instances. Keep the provider-facing client
    # snapshot detached so a concurrent server deletion cannot make token
    # exchange or compensation reload stale ORM state.
    db.expunge(client)
    claim_error = _claim_mcp_oauth_flow_state(db, flow_state)
    if claim_error is not None:
        error_code, message = claim_error
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code=error_code,
            message=message,
        )
    if error:
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code="token_exchange_failed",
            message=oauth_error_message(
                {"error": error}, "MCP OAuth authorization failed"
            ),
        )
    if not code:
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code="invalid_state",
            message="Missing authorization code",
        )
    issued_token: _MCPOAuthIssuedTokenSnapshot | None = None
    failure_stage = "token_exchange"
    try:
        token_data = await _exchange_mcp_oauth_code(
            client=client,
            code=code,
            code_verifier=code_verifier,
            resource=resource,
        )
        failure_stage = "snapshot_issued_token"
        issued_token = _mcp_oauth_issued_token_snapshot(
            client=client,
            flow_id=flow_identity.id,
            token_data=token_data,
        )
        failure_stage = "lock_lifecycle"
        lifecycle = _lock_active_mcp_oauth_lifecycle(
            db,
            association_identity=association_identity,
            flow_identity=flow_identity,
        )
        if lifecycle is None:
            await _rollback_and_revoke_mcp_oauth_issued_token(db, issued_token)
            return _mcp_oauth_callback_error_redirect_for_path(
                callback_redirect_after,
                error_code="invalid_state",
                message="MCP server connection changed during OAuth authorization",
            )
        locked_flow_state = lifecycle[2]
        if locked_flow_state is None:
            raise RuntimeError("MCP OAuth lifecycle lock did not return the flow")
        failure_stage = "persist_grant"
        _upsert_mcp_oauth_grant(
            db,
            flow_state=locked_flow_state,
            token_data=token_data,
        )
        failure_stage = "commit_grant"
        db.commit()
    except HTTPException as exc:
        if issued_token is not None:
            await _rollback_and_revoke_mcp_oauth_issued_token(db, issued_token)
        else:
            db.rollback()
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        error_code = str(detail.get("code") or "token_exchange_failed")
        message = str(detail.get("message") or "MCP OAuth authorization failed")
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code=error_code,
            message=message,
        )
    except Exception as exc:
        if issued_token is not None:
            await _rollback_and_revoke_mcp_oauth_issued_token(db, issued_token)
        else:
            db.rollback()
        logger.error(
            "MCP OAuth callback failed after state claim (stage=%s, exception_type=%s)",
            failure_stage,
            type(exc).__name__,
        )
        return _mcp_oauth_callback_error_redirect_for_path(
            callback_redirect_after,
            error_code="token_exchange_failed",
            message="MCP OAuth authorization failed",
        )

    # Positive success signal: the connect popup's self-close logic keys on
    # this param instead of inferring success from "no error params", so a
    # future error param added to the error redirect can't be mistaken for
    # success by an out-of-date guard.
    response = RedirectResponse(
        _mcp_oauth_redirect_after_url(
            _redirect_after_with_params(
                callback_redirect_after,
                (("mcp_oauth_success", "1"),),
            )
        )
    )
    _clear_mcp_oauth_state_cookie(response)
    return response


@mcp_router.post("/{server_id}/oauth/discover", response_model=MCPOAuthDiscoverResponse)
async def discover_mcp_oauth(
    server_id: int,
    request_data: MCPOAuthDiscoverRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPOAuthDiscoverResponse:
    """Discover MCP OAuth protected-resource and authorization-server metadata."""
    _, server = _get_user_mcp_server_or_404(
        db,
        user_id=cast(int, current_user.id),
        server_id=server_id,
        require_active=True,
    )
    auth_config = _get_mcp_oauth_config(server)
    discovery = await _discover_mcp_oauth_for_server(server, auth_config)
    return _mcp_oauth_discovery_response(discovery)


@mcp_router.post("/{server_id}/oauth/connect", response_model=None)
async def connect_mcp_oauth(
    server_id: int,
    request_data: MCPOAuthConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    accept: Annotated[str | None, Header()] = None,
) -> RedirectResponse | JSONResponse:
    """Start MCP OAuth Authorization Code + PKCE for the current user."""
    return await _connect_mcp_oauth_for_owner(
        server_id,
        request_data,
        current_user,
        db,
        resource_owner_key=_default_resource_owner_key(cast(int, current_user.id)),
        accept=accept,
        persistence=_OAuthPersistence.COMMIT,
    )


async def _connect_mcp_oauth_for_owner(
    server_id: int,
    request_data: MCPOAuthConnectRequest,
    current_user: User,
    db: Session,
    *,
    resource_owner_key: str,
    accept: str | None,
    persistence: _OAuthPersistence,
) -> RedirectResponse | JSONResponse:
    """Start OAuth for one exact owner with an explicit transaction owner."""
    user_id = cast(int, current_user.id)
    association, server = _get_user_mcp_server_or_404(
        db,
        user_id=user_id,
        server_id=server_id,
        require_active=persistence is _OAuthPersistence.COMMIT,
    )
    association_generation = association.lifecycle_generation
    if not isinstance(association_generation, UUID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oauth_lifecycle_changed",
                "message": "MCP server connection lifecycle is unavailable",
            },
        )
    association_identity = _MCPOAuthAssociationIdentity(
        server_id=server_id,
        user_id=user_id,
        lifecycle_generation=association_generation,
    )
    auth_config = _get_mcp_oauth_config(server)
    discovery = await _discover_mcp_oauth_for_server(server, auth_config)

    redirect_uri = (
        _configured_mcp_oauth_value(None, auth_config, "redirect_uri")
        or _default_mcp_oauth_redirect_uri()
    )
    redirect_uri = _bounded_mcp_oauth_value(redirect_uri, field_name="redirect_uri")
    selected_issuer = _bounded_mcp_oauth_value(
        str(discovery.authorization_server.issuer), field_name="issuer"
    )
    registration_lookup_hash: str | None = None
    client_id = _configured_mcp_oauth_value(None, auth_config, "client_id")
    client_secret = _configured_mcp_oauth_value(None, auth_config, "client_secret")
    if client_id:
        client_id = _bounded_mcp_oauth_value(client_id, field_name="client_id")
        token_endpoint_auth_method = str(
            auth_config.get("token_endpoint_auth_method")
            or ("client_secret_post" if client_secret else "none")
        )
        token_endpoint_auth_method = _bounded_mcp_oauth_value(
            token_endpoint_auth_method,
            field_name="token_endpoint_auth_method",
            max_length=MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD_MAX_LENGTH,
        )
        if token_endpoint_auth_method not in MCP_OAUTH_TOKEN_ENDPOINT_AUTH_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "unsupported_auth_server",
                    "message": (
                        f"Unsupported token endpoint auth method: {token_endpoint_auth_method}"
                    ),
                },
            )
    else:
        registration_lookup_hash = mcp_oauth_client_registration_lookup_hash(
            server_id,
            selected_issuer,
            redirect_uri,
        )
        registered_client = (
            db.query(MCPOAuthClient)
            .filter(
                MCPOAuthClient.registration_lookup_hash == registration_lookup_hash,
            )
            .first()
        )
        if registered_client is not None:
            client_id = str(registered_client.client_id)
            client_secret = None
            token_endpoint_auth_method = str(
                registered_client.token_endpoint_auth_method
            )
        else:
            # Public requests own their transaction and release it before I/O.
            if persistence is _OAuthPersistence.COMMIT:
                db.rollback()
            try:
                registration = await register_mcp_oauth_public_client(
                    discovery.authorization_server,
                    redirect_uri=redirect_uri,
                )
            except MCPOAuthDiscoveryError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": exc.code, "message": exc.message},
                ) from exc
            client_id = registration.client_id
            client_secret = None
            token_endpoint_auth_method = registration.token_endpoint_auth_method
    selected_scope = _scope_string(auth_config.get("scope") or discovery.scopes)
    resource_owner_key = _bounded_mcp_oauth_value(
        resource_owner_key,
        field_name="resource_owner_key",
        max_length=MCP_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH,
    )
    selected_resource = _bounded_mcp_oauth_value(
        str(discovery.resource), field_name="resource"
    )

    persisted = _persist_mcp_oauth_connect_flow(
        db,
        association_identity=association_identity,
        discovery=discovery,
        client_id=client_id,
        client_secret=client_secret,
        token_endpoint_auth_method=token_endpoint_auth_method,
        redirect_uri=redirect_uri,
        registration_lookup_hash=registration_lookup_hash,
        resource_owner_key=resource_owner_key,
        selected_issuer=selected_issuer,
        selected_resource=selected_resource,
        selected_scope=selected_scope,
        redirect_after=request_data.redirect_after,
        persistence=persistence,
    )
    if persisted is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oauth_lifecycle_changed",
                "message": "MCP server connection changed during OAuth setup",
            },
        )
    persisted_client_id, state_value, code_verifier = persisted

    params = {
        "response_type": "code",
        "client_id": persisted_client_id,
        "redirect_uri": redirect_uri,
        "state": state_value,
        "code_challenge": _pkce_code_challenge(code_verifier),
        "code_challenge_method": "S256",
        "resource": selected_resource,
    }
    if selected_scope:
        params["scope"] = selected_scope
    authorization_url = _oauth_authorization_url(
        discovery.authorization_server.authorization_endpoint, params
    )
    if accept and "application/json" in accept.lower():
        json_response = JSONResponse({"authorization_url": authorization_url})
        _set_mcp_oauth_state_cookie(json_response, state_value)
        return json_response
    redirect_response = RedirectResponse(
        authorization_url, status_code=status.HTTP_303_SEE_OTHER
    )
    _set_mcp_oauth_state_cookie(redirect_response, state_value)
    return redirect_response


@mcp_router.get("/{server_id}/oauth/status", response_model=MCPOAuthStatusResponse)
async def get_mcp_oauth_status(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPOAuthStatusResponse:
    """Return MCP OAuth grants owned by the current user for one MCP server."""
    user_id = cast(int, current_user.id)
    _, server = _get_user_mcp_server_or_404(
        db, user_id=user_id, server_id=server_id, require_active=True
    )
    config = server.to_config_dict()
    auth_config = config.get("auth") if isinstance(config.get("auth"), dict) else {}
    grants = select_mcp_oauth_grants(
        db,
        server_id=server_id,
        user_id=user_id,
        auth_config=auth_config if isinstance(auth_config, dict) else {},
        resource_owner_key=_default_resource_owner_key(user_id),
    )
    return MCPOAuthStatusResponse(
        server_id=server_id,
        auth_type=auth_config.get("type") if isinstance(auth_config, dict) else None,
        resource=auth_config.get("resource") if isinstance(auth_config, dict) else None,
        issuer=auth_config.get("issuer") if isinstance(auth_config, dict) else None,
        scope=auth_config.get("scope") if isinstance(auth_config, dict) else None,
        grants=[_mcp_oauth_grant_response(grant) for grant in grants],
    )


async def revoke_mcp_oauth_grants_for_owner(
    server_id: int,
    current_user: User,
    db: Session,
    *,
    resource_owner_key: str,
) -> MCPOAuthOwnerRevocation:
    """Stage exact-owner revocation in the caller-owned transaction."""

    user_id = cast(int, current_user.id)
    owner_key = _trusted_mcp_oauth_owner_key(resource_owner_key, user_id=user_id)
    _get_user_mcp_server_or_404(
        db,
        user_id=user_id,
        server_id=server_id,
        require_active=False,
    )
    db.query(MCPOAuthFlowState).filter(
        MCPOAuthFlowState.mcp_server_id == server_id,
        MCPOAuthFlowState.user_id == user_id,
        MCPOAuthFlowState.resource_owner_key == owner_key,
    ).delete(synchronize_session=False)
    grants = (
        db.query(MCPOAuthGrant)
        .filter(
            MCPOAuthGrant.mcp_server_id == server_id,
            MCPOAuthGrant.user_id == user_id,
            MCPOAuthGrant.resource_owner_key == owner_key,
            MCPOAuthGrant.status == "active",
        )
        .all()
    )
    snapshots = tuple(
        _mcp_oauth_grant_revocation_snapshot(client=grant.oauth_client, grant=grant)
        for grant in grants
        if isinstance(grant.oauth_client, MCPOAuthClient)
    )
    now = _utc_now()
    for grant in grants:
        setattr(grant, "status", "revoked")
        setattr(grant, "revoked_at", now)
    db.flush()
    return MCPOAuthOwnerRevocation(
        grant_count=len(grants),
        _snapshots=snapshots,
    )


@mcp_router.delete(
    "/{server_id}/oauth/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_mcp_oauth_grant(
    server_id: int,
    grant_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Revoke an MCP OAuth grant owned by the current user."""
    user_id = cast(int, current_user.id)
    _get_user_mcp_server_or_404(
        db, user_id=user_id, server_id=server_id, require_active=True
    )
    grant = (
        db.query(MCPOAuthGrant)
        .filter(
            MCPOAuthGrant.id == grant_id,
            MCPOAuthGrant.mcp_server_id == server_id,
            MCPOAuthGrant.user_id == user_id,
            MCPOAuthGrant.resource_owner_key == _default_resource_owner_key(user_id),
        )
        .first()
    )
    if not grant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="MCP OAuth grant not found"
        )
    if isinstance(grant.oauth_client, MCPOAuthClient):
        await _revoke_mcp_oauth_grant_externally(
            client=grant.oauth_client,
            grant=grant,
        )
    setattr(grant, "status", "revoked")
    setattr(grant, "revoked_at", _utc_now())
    db.commit()
