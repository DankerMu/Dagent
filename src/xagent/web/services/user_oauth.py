"""Owner-scoped access to persisted OAuth credentials.

All ordinary callers pass ``resource_owner_key=None``. Trusted actor callers
pass an exact server-derived key. Centralizing the predicate prevents a direct
ID lookup or provider list from accidentally widening into another namespace.
"""

from __future__ import annotations

from typing import Any

from ..models.user_oauth import USER_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH


def normalize_user_oauth_resource_owner_key(value: Any) -> str | None:
    """Return one valid owner key, preserving ``None`` as ordinary ownership."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("resource_owner_key must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("resource_owner_key must not be blank")
    if len(normalized) > USER_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH:
        raise ValueError(
            "resource_owner_key exceeds "
            f"{USER_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH} characters"
        )
    return normalized
