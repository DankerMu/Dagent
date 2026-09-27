from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Callable, cast

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models.tool_config import UserToolConfig

# Hook signature: (db: Session, user: Any) -> dict[str, {"enabled": bool|None}]
# Returns per-user overrides for tool enable/disable.
# Application layers can inject per-user tool policies via set_user_tool_overrides_hook().
# NOTE: "config" override is reserved for future use and not yet implemented.
_get_user_tool_overrides_hook: Callable[[Session, Any], dict] | None = None


def set_user_tool_overrides_hook(hook: Callable[[Session, Any], dict] | None) -> None:
    global _get_user_tool_overrides_hook
    _get_user_tool_overrides_hook = hook


def get_user_tool_overrides(db: Session, user: Any) -> dict:
    if _get_user_tool_overrides_hook is not None:
        return _get_user_tool_overrides_hook(db, user)
    return {}


# Hook signature: (db: Session, user: Any) -> list[str] | None
# Returns a positive tool allowlist for the current execution: only tools whose
# name is in the list are kept. ``None`` means "no allowlist configured" (no
# filtering); an empty list means "no tools allowed". Unlike the disable-set
# override hook above, this filters positively against the already-built tool
# list, so dynamically-loaded MCP tools are covered without enumerating a tool
# universe. Application layers inject it via set_user_tool_allowlist_hook().
_get_user_tool_allowlist_hook: Callable[[Session, Any], list[str] | None] | None = None


def set_user_tool_allowlist_hook(
    hook: Callable[[Session, Any], list[str] | None] | None,
) -> None:
    global _get_user_tool_allowlist_hook
    _get_user_tool_allowlist_hook = hook


def get_user_tool_allowlist(db: Session, user: Any) -> list[str] | None:
    if _get_user_tool_allowlist_hook is not None:
        return _get_user_tool_allowlist_hook(db, user)
    return None


def has_user_tool_policy_hooks() -> bool:
    """Return whether an application registered either runtime policy hook."""
    return (
        _get_user_tool_overrides_hook is not None
        or _get_user_tool_allowlist_hook is not None
    )


def has_user_tool_overrides_hook() -> bool:
    """Return whether an application registered the overrides hook specifically.

    A caller deciding that an overrides read was *unresolvable* needs this
    rather than :func:`has_user_tool_policy_hooks`: with no overrides hook
    registered, ``get_user_tool_overrides`` returns ``{}`` without consulting
    ``user`` at all, so a missing runtime user resolves that input rather than
    leaving it unresolved.
    """
    return _get_user_tool_overrides_hook is not None


# The allowlist value that means "policy could not be resolved". A registering
# application enforces authorization through the hooks above, so a failed or
# missing runtime ``User`` reload must not be reported as "no policy
# configured": that would build the globally available tool set and run the
# turn unrestricted. Resolving to an empty allowlist reuses the hook's existing
# "an empty list means no tools allowed" contract, so every consumer that
# already honours a concrete allowlist fails closed with no new branch.
#
# Only meaningful while ``has_user_tool_policy_hooks()`` is true. With no hook
# registered there is no policy to lose, and standalone xagent keeps its
# unrestricted default.
#
# A tuple so the constant itself cannot be mutated in place: this is the value
# that denies every tool, and a caller that appended to it would silently widen
# what an unresolved policy grants.
TOOL_POLICY_UNAVAILABLE_ALLOWLIST: tuple[str, ...] = ()


def unresolved_tool_policy_allowlist() -> list[str] | None:
    """Return the fail-closed allowlist for an unresolvable policy read.

    ``None`` (no filtering) when no application hook is registered, so the
    fail-closed behaviour is scoped to deployments that actually delegate
    authorization to the hooks.

    Returns a fresh ``list`` because the allowlist contract consumers implement
    is ``Optional[list]``; the immutable constant above is the source value.
    """
    if not has_user_tool_policy_hooks():
        return None
    return list(TOOL_POLICY_UNAVAILABLE_ALLOWLIST)


SQL_CONNECTION_ENV_PREFIX = "XAGENT_EXTERNAL_DB_"
ALLOWED_SQL_SCHEMES = {"postgresql", "mysql", "mariadb", "mssql", "sqlite"}


def _build_fernet_key() -> bytes:
    raw = (
        os.getenv("XAGENT_SECRET_ENCRYPTION_KEY")
        or os.getenv("SECRET_KEY")
        or "xagent-dev-key"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_build_fernet_key())


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def _mask_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def _get_or_create_user_tool_config(
    db: Session, user_id: int, tool_name: str
) -> UserToolConfig:
    config = (
        db.query(UserToolConfig)
        .filter(
            UserToolConfig.user_id == user_id,
            UserToolConfig.tool_name == tool_name,
        )
        .first()
    )
    if config:
        return config

    config = UserToolConfig(user_id=user_id, tool_name=tool_name, config={})
    db.add(config)
    db.flush()
    return config


def _get_user_tool_payload(config: UserToolConfig) -> dict[str, Any]:
    raw_config = cast(Any, getattr(config, "config", None))
    payload: dict[str, Any] = {}
    if isinstance(raw_config, dict):
        for key, value in raw_config.items():
            payload[str(key)] = value
    cast(Any, config).config = payload
    return payload


def _sanitize_sql_connection_name(name: str) -> str:
    return name.strip().upper()


def _get_user_sql_connection_store(config: UserToolConfig) -> dict[str, Any]:
    payload = _get_user_tool_payload(config)
    sql_connections = payload.get("sql_connections")
    if not isinstance(sql_connections, dict):
        sql_connections = {}
    payload["sql_connections"] = sql_connections
    cast(Any, config).config = payload
    return sql_connections


def _sql_url_mask(url: str) -> str:
    try:
        parsed = make_url(url)
        return parsed.render_as_string(hide_password=True)
    except Exception:
        return _mask_value(url)


def _get_user_sql_tool_config(db: Session, user_id: int) -> UserToolConfig:
    return _get_or_create_user_tool_config(db, user_id, "sql_query")


def set_sql_connection(
    db: Session, user_id: int, name: str, connection_url: str
) -> None:
    normalized_name = _sanitize_sql_connection_name(name)
    if not normalized_name:
        raise ValueError("Connection name is required")
    normalized_url = connection_url.strip()
    if not normalized_url:
        raise ValueError("Connection URL is required")
    try:
        parsed = make_url(normalized_url)
    except Exception as exc:
        raise ValueError("Invalid SQLAlchemy connection URL") from exc

    base_scheme = str(parsed.drivername).split("+", 1)[0].lower()
    if base_scheme not in ALLOWED_SQL_SCHEMES:
        allowed_schemes_text = ", ".join(sorted(ALLOWED_SQL_SCHEMES))
        raise ValueError(
            f"Unsupported SQLAlchemy URL scheme '{parsed.drivername}'. "
            f"Allowed schemes: {allowed_schemes_text}"
        )

    config = _get_user_sql_tool_config(db, user_id)
    storage = _get_user_sql_connection_store(config)
    now = datetime.now(timezone.utc).isoformat()
    storage[normalized_name] = {
        "ciphertext": _encrypt(normalized_url),
        "masked": _sql_url_mask(normalized_url),
        "updated_at": now,
    }
    db.add(config)
    flag_modified(config, "config")
    db.commit()


def delete_sql_connection(db: Session, user_id: int, name: str) -> None:
    normalized_name = _sanitize_sql_connection_name(name)
    config = (
        db.query(UserToolConfig)
        .filter(
            UserToolConfig.user_id == user_id,
            UserToolConfig.tool_name == "sql_query",
        )
        .first()
    )
    if not config:
        return
    payload = cast(Any, getattr(config, "config", None))
    if not isinstance(payload, dict):
        return
    sql_connections = payload.get("sql_connections")
    if not isinstance(sql_connections, dict):
        return
    removed = False
    for key in list(sql_connections.keys()):
        if (
            isinstance(key, str)
            and _sanitize_sql_connection_name(key) == normalized_name
        ):
            del sql_connections[key]
            removed = True

    if removed:
        cast(Any, config).config = payload
        db.add(config)
        flag_modified(config, "config")
        db.commit()


def resolve_sql_connection(db: Session, user_id: int | None, name: str) -> str | None:
    normalized_name = _sanitize_sql_connection_name(name)
    config = None
    if user_id is not None:
        config = (
            db.query(UserToolConfig)
            .filter(
                UserToolConfig.user_id == user_id,
                UserToolConfig.tool_name == "sql_query",
            )
            .first()
        )
    if config and isinstance(config.config, dict):
        sql_connections = config.config.get("sql_connections")
        if isinstance(sql_connections, dict):
            item = sql_connections.get(normalized_name)
            if isinstance(item, dict) and isinstance(item.get("ciphertext"), str):
                decrypted = _decrypt(item["ciphertext"])
                if decrypted:
                    return decrypted

    return os.getenv(f"{SQL_CONNECTION_ENV_PREFIX}{normalized_name}")


def get_sql_connection_map(db: Session, user_id: int | None) -> dict[str, str]:
    result: dict[str, str] = {}

    for key, value in os.environ.items():
        if key.startswith(SQL_CONNECTION_ENV_PREFIX) and value:
            name = key[len(SQL_CONNECTION_ENV_PREFIX) :]
            if name:
                result[name] = value

    if user_id is None:
        return result

    config = (
        db.query(UserToolConfig)
        .filter(
            UserToolConfig.user_id == user_id,
            UserToolConfig.tool_name == "sql_query",
        )
        .first()
    )
    if config and isinstance(config.config, dict):
        sql_connections = config.config.get("sql_connections")
        if isinstance(sql_connections, dict):
            for raw_name, item in sql_connections.items():
                if not isinstance(raw_name, str) or not isinstance(item, dict):
                    continue
                ciphertext = item.get("ciphertext")
                if isinstance(ciphertext, str):
                    decrypted = _decrypt(ciphertext)
                    if decrypted:
                        result[_sanitize_sql_connection_name(raw_name)] = decrypted

    return result


def list_sql_connections(db: Session, user_id: int | None) -> list[dict[str, Any]]:
    env_names = {
        key[len(SQL_CONNECTION_ENV_PREFIX) :]: value
        for key, value in os.environ.items()
        if key.startswith(SQL_CONNECTION_ENV_PREFIX) and value
    }

    db_entries: dict[str, dict[str, Any]] = {}
    config = None
    if user_id is not None:
        config = (
            db.query(UserToolConfig)
            .filter(
                UserToolConfig.user_id == user_id,
                UserToolConfig.tool_name == "sql_query",
            )
            .first()
        )
    if config and isinstance(config.config, dict):
        sql_connections = config.config.get("sql_connections")
        if isinstance(sql_connections, dict):
            for raw_name, item in sql_connections.items():
                if isinstance(raw_name, str) and isinstance(item, dict):
                    db_entries[_sanitize_sql_connection_name(raw_name)] = item

    all_names = sorted(set(env_names.keys()) | set(db_entries.keys()))
    output: list[dict[str, Any]] = []
    for name in all_names:
        db_item = db_entries.get(name)
        if db_item:
            masked = str(db_item.get("masked") or "")
            source = "db"
        else:
            env_value = env_names.get(name, "")
            masked = _sql_url_mask(env_value)
            source = "env" if env_value else "none"

        output.append(
            {
                "name": name,
                "source": source,
                "masked": masked,
                "configured": bool(masked),
            }
        )

    return output
