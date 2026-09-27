"""Generic MCP OAuth owner keys are exact, bounded credential namespaces."""

import pytest

from xagent.web.models.user_oauth import USER_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH
from xagent.web.services.mcp_runtime import MCPActorAuthorizationPolicy
from xagent.web.services.user_oauth import normalize_user_oauth_resource_owner_key


def test_ordinary_and_actor_oauth_owner_keys_remain_distinct():
    assert normalize_user_oauth_resource_owner_key(None) is None
    assert normalize_user_oauth_resource_owner_key("  actor:local-run  ") == (
        "actor:local-run"
    )
    assert MCPActorAuthorizationPolicy("  actor:local-run  ").resource_owner_key == (
        "actor:local-run"
    )


def test_invalid_oauth_owner_key_cannot_open_a_different_credential_namespace():
    for invalid in (42, "  ", "x" * (USER_OAUTH_RESOURCE_OWNER_KEY_MAX_LENGTH + 1)):
        with pytest.raises(ValueError, match="resource_owner_key"):
            normalize_user_oauth_resource_owner_key(invalid)
        with pytest.raises(ValueError, match="resource_owner_key"):
            MCPActorAuthorizationPolicy(invalid)


def test_actor_authorization_policy_rejects_nonboolean_privileged_flag():
    with pytest.raises(ValueError, match="allow_builtin_stdio must be a boolean"):
        MCPActorAuthorizationPolicy("actor:local-run", allow_builtin_stdio=1)
