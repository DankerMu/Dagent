"""Team visibility hooks cannot grant control of hidden MCP definitions."""

from types import SimpleNamespace

import pytest

from xagent.web.services.connector_team_scope import (
    TEAM_OWNED_MCP_DEFINITIONS_KEY,
    connector_visible_to_user,
    set_connector_team_hooks,
    snapshot_connector_team_hooks,
    team_connector_selection,
)


def test_team_hook_cannot_claim_hidden_or_malformed_definition_ownership():
    with snapshot_connector_team_hooks():
        for owned in ({2}, {True}, [1]):
            set_connector_team_hooks(
                team_visibility=lambda db, *, team_id, owned=owned: {
                    "mcp": {1},
                    "custom_api": set(),
                    TEAM_OWNED_MCP_DEFINITIONS_KEY: owned,
                }
            )
            with pytest.raises(ValueError, match="team visibility hook returned"):
                team_connector_selection(None, team_id=7)


def test_deactivated_personal_link_does_not_hide_team_owned_connector():
    with snapshot_connector_team_hooks():
        set_connector_team_hooks(
            team_visibility=lambda db, *, team_id: {
                "mcp": {1},
                "custom_api": set(),
                TEAM_OWNED_MCP_DEFINITIONS_KEY: {1},
            }
        )
        selection = team_connector_selection(None, team_id=7)
        assert selection.owned_mcp_definition_ids == frozenset({1})
        assert connector_visible_to_user(
            association=SimpleNamespace(is_active=False),
            connector_id=1,
            team_ids=selection.mcp_ids,
        )
        assert not connector_visible_to_user(
            association=SimpleNamespace(is_active=False),
            connector_id=2,
            team_ids=selection.mcp_ids,
        )
        assert connector_visible_to_user(
            association=SimpleNamespace(is_active=True),
            connector_id=2,
            team_ids=selection.mcp_ids,
        )
