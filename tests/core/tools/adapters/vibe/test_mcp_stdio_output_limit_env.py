"""A stdio MCP connector subprocess and the parent process's own
``OutputFilteredToolWrapper`` each independently bound tool output length:
the child reads ``XAGENT_TOOL_MAX_OUTPUT_LENGTH`` from its OWN environment
to build a bounded, valid-JSON result; the parent reads the same-named env
var (or a per-config override) from ITS OWN environment to decide where to
blindly character-slice that result. ``_create_stdio_session`` launches the
child with a minimal, explicitly-built env (credentials + caller id only),
never inheriting the parent's environment, so the two budgets can silently
disagree -- and when the parent's budget is smaller, its blind slice can cut
the child's already-valid JSON mid-structure.

``create_mcp_tools`` closes this by mirroring the parent's own effective
budget into every stdio config's env before tools are built from it. The
parent's own filter (``ToolFactory._apply_output_filters``) computes its
budget once from ``config.get_max_output_length()`` and applies it
uniformly to every tool -- there is no per-server override on the parent
side. A config's own env can still carry a legitimate, narrower cap for
one connector (e.g. an operator deliberately limiting a noisy one below
the parent's default) -- that's never a corruption risk, since the
parent's blind slice only fires once a response is at or beyond ITS
budget, so a child bounded below that is never touched. A pre-existing
value is therefore PRESERVED exactly when it's a valid positive integer
at or below the parent's effective budget; anything else (missing, not a
positive integer, or LARGER than the parent's budget) is overwritten with
the parent's effective value -- a larger existing value in particular
would otherwise let the parent's blind slice cut a child response sized
to a bigger, unaligned budget, exactly reproducing the bug this mechanism
exists to fix.

Only ``XAGENT_TOOL_MAX_OUTPUT_LENGTH`` is mirrored: no builtin connector
reads a field-count/recursion-depth env var, so mirroring those would only
widen the injection surface for zero present benefit.
"""

import json
import logging

import pytest

from xagent.config import TOOL_MAX_OUTPUT_LENGTH
from xagent.core.tools.adapters.vibe.config import MCPFailurePolicy
from xagent.core.tools.adapters.vibe.factory import ToolFactory
from xagent.core.tools.adapters.vibe.mcp_tools import (
    _apply_stdio_output_limit_env,
    create_mcp_tools,
)
from xagent.core.tools.adapters.vibe.output_filter import OutputValueFilter


class _FakeConfig:
    def __init__(
        self,
        mcp_configs,
        *,
        max_output_length=12345,
    ):
        self._mcp_configs = mcp_configs
        self._max_output_length = max_output_length

    def get_tool_selection_spec(self):
        return None

    async def get_mcp_server_configs(self):
        return self._mcp_configs

    def get_mcp_failure_policy(self):
        return MCPFailurePolicy.BEST_EFFORT

    def get_sandbox(self):
        return None

    def get_max_output_length(self):
        return self._max_output_length


def _capture_create(monkeypatch, captured):
    async def fake_create(mcp_configs, sandbox=None):
        captured["mcp_configs"] = mcp_configs
        return []

    monkeypatch.setattr(
        ToolFactory, "_create_mcp_tools_from_configs", staticmethod(fake_create)
    )


@pytest.mark.asyncio
async def test_stdio_configs_receive_the_parents_effective_output_limit(monkeypatch):
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {"command": "python", "args": ["-m", "probe"]},
            }
        ]
    )
    await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    env = cfg["config"]["env"]
    assert env[TOOL_MAX_OUTPUT_LENGTH] == "12345"


@pytest.mark.asyncio
async def test_other_env_entries_survive_untouched(monkeypatch):
    """Credentials and caller-id env already placed on the config (e.g. by
    the actor-owned/team-owned loaders) must survive untouched -- only the
    output-limit key itself is ever touched."""
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {
                    "command": "python",
                    "args": [],
                    "env": {"XAGENT_MCP_CALLER_ID": "user-1"},
                },
            }
        ]
    )
    await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    env = cfg["config"]["env"]
    assert env["XAGENT_MCP_CALLER_ID"] == "user-1"
    assert env[TOOL_MAX_OUTPUT_LENGTH] == "12345"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing",
    ["4096", 4096, "12345", 12345],
    ids=["smaller-str", "smaller-int", "equal-str", "equal-int"],
)
async def test_valid_override_at_or_below_parent_budget_is_preserved(
    monkeypatch, caplog, existing
):
    """A config's own env can carry a legitimate, narrower cap for one
    connector (e.g. an operator deliberately limiting a noisy one below the
    parent's default) -- overwriting it unconditionally would silently
    widen a resource bound someone set on purpose, even though it poses no
    corruption risk: the parent's blind slice only fires once a response
    is at or beyond ITS budget, so a child bounded at or below that is
    never touched. It must survive, canonicalized to a plain string, with
    no warning (nothing here is actually wrong)."""
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {
                    "command": "python",
                    "args": [],
                    "env": {TOOL_MAX_OUTPUT_LENGTH: existing},
                },
            }
        ]
    )
    with caplog.at_level(logging.WARNING):
        await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    env = cfg["config"]["env"]
    assert env[TOOL_MAX_OUTPUT_LENGTH] == str(int(existing))
    assert not any("isn't a valid" in r.message for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing",
    [
        99999,  # LARGER than the parent's effective budget (12345): the
        "99999",  # actual bug this whole mechanism exists to fix -- a
        # valid, larger child budget must not survive, or the parent's
        # blind slice (which knows only its own, smaller budget) still
        # corrupts a response sized to this one.
        True,
        False,
        3.7,
        "3.7",
        0,
        "0",
        -5,
        "-5",
        "not-a-number",
    ],
    ids=[
        "larger-int",
        "larger-str",
        "bool-true",
        "bool-false",
        "float",
        "str-float",
        "zero-int",
        "zero-str",
        "negative-int",
        "negative-str",
        "non-numeric-str",
    ],
)
async def test_invalid_or_larger_existing_override_is_overwritten_and_warned(
    monkeypatch, caplog, existing
):
    """Anything that ISN'T a valid, positive integer at or below the
    parent's own effective budget must be overwritten with that effective
    value rather than preserved. A larger value in particular would leave
    the child able to produce a response the parent's blind slice still
    cuts mid-structure, exactly reproducing the bug this mechanism exists
    to fix; the rest (wrong type, non-numeric, non-positive) would simply
    make the real child-side getter fall back to its own default anyway."""
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {
                    "command": "python",
                    "args": [],
                    "env": {TOOL_MAX_OUTPUT_LENGTH: existing},
                },
            }
        ]
    )
    with caplog.at_level(logging.WARNING):
        await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    env = cfg["config"]["env"]
    assert env[TOOL_MAX_OUTPUT_LENGTH] == "12345"
    assert any("isn't a valid" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_existing_override_already_matching_is_not_re_warned(monkeypatch, caplog):
    """An existing value that already equals the parent's effective budget
    (e.g. this function ran once already, or the value was set to match on
    purpose) is preserved without spamming a warning on every call -- only
    an actual replacement is worth flagging."""
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {
                    "command": "python",
                    "args": [],
                    "env": {TOOL_MAX_OUTPUT_LENGTH: "12345"},
                },
            }
        ]
    )
    with caplog.at_level(logging.WARNING):
        await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    assert cfg["config"]["env"][TOOL_MAX_OUTPUT_LENGTH] == "12345"
    assert not any("isn't a valid" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_non_dict_env_is_skipped_and_warned(monkeypatch, caplog):
    """A malformed, non-dict env (e.g. a raw "KEY=value" string some
    caller wrote instead of a mapping) must be left alone rather than
    crash trying to treat it as a mapping, and the skip is logged so it's
    diagnosable in production."""
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {"command": "python", "args": [], "env": "FOO=1"},
            }
        ]
    )
    with caplog.at_level(logging.WARNING):
        await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    assert cfg["config"]["env"] == "FOO=1"
    assert any("non-dict env" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_non_stdio_configs_are_left_untouched(monkeypatch):
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "http-server",
                "transport": "streamable_http",
                "config": {"url": "https://example.com/mcp"},
            }
        ]
    )
    await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    assert "env" not in cfg["config"]


@pytest.mark.asyncio
async def test_unavailable_placeholder_is_left_untouched(monkeypatch):
    """Production never produces transport="stdio" + config.unavailable=True
    -- ``_build_unavailable_mcp_config`` always uses transport="unavailable"
    -- so this is the real shape the transport check must pass through
    untouched."""
    captured: dict = {}
    _capture_create(monkeypatch, captured)

    config = _FakeConfig(
        [
            {
                "name": "broken",
                "transport": "unavailable",
                "config": {"unavailable": True, "reason": "oauth_token_required"},
            }
        ]
    )
    await create_mcp_tools(config)

    (cfg,) = captured["mcp_configs"]
    assert "env" not in cfg["config"]


def test_input_configs_are_never_mutated_in_place():
    """Load-bearing per the function's own contract: a config's
    request-scoped cache may hand back and reuse the same dict objects
    across repeated calls within one request, so mutating them in place
    would let one call's injection leak into (or be fooled by) another's."""
    original_env: dict = {}
    original_inner = {"command": "python", "args": [], "env": original_env}
    original_cfg = {"name": "jira", "transport": "stdio", "config": original_inner}
    config = _FakeConfig([])

    result_a = _apply_stdio_output_limit_env([original_cfg], config)
    result_b = _apply_stdio_output_limit_env([original_cfg], config)

    assert original_inner["env"] is original_env
    assert original_env == {}
    assert result_a[0]["config"]["env"][TOOL_MAX_OUTPUT_LENGTH] == "12345"
    assert result_b[0]["config"]["env"][TOOL_MAX_OUTPUT_LENGTH] == "12345"
    assert result_a[0] is not original_cfg
    assert result_a[0]["config"] is not original_inner


def _child_bounded_json(budget: int) -> str:
    """Build a valid JSON string no longer than ``budget``, the way a real
    connector's own capping logic (e.g. ``success_with_capped_dict``)
    would: add items until the next one would exceed the budget, never by
    slicing a larger string mid-structure. A naive ``payload[:budget]``
    slice is exactly the failure mode this whole mechanism exists to
    prevent, so it must not also be how these tests build a "child"
    output -- that would prove nothing about JSON validity either way."""
    items: list[str] = []
    while True:
        candidate = json.dumps({"issues": [*items, f"issue-{len(items)}"]})
        if len(candidate) > budget:
            break
        items.append(f"issue-{len(items)}")
    return json.dumps({"issues": items})


def test_mismatched_budget_corrupts_json_but_mirrored_budget_does_not():
    """Differential regression for the bug this PR fixes.

    Before the fix, the child sizes its own bounded, valid-JSON output
    against whatever budget IT reads from its own environment (simulated
    here as a distinct value the parent never told it about), and the
    parent's own ``OutputValueFilter`` -- which knows only ITS budget --
    blindly slices that already-valid JSON at a different boundary,
    corrupting it.

    After the fix, ``_apply_stdio_output_limit_env`` (the real production
    function, not a stand-in) is what determines what the child would
    read: a child sized to that mirrored value and a parent filtering with
    the same budget never disagree, so the filter never touches it and the
    result stays valid JSON.
    """
    parent_budget = 120
    unmirrored_child_budget = 400  # what the child would use without this PR

    # Before: the child's own budget has nothing to do with the parent's.
    # It builds a valid, larger response the parent was never told about.
    child_output_before = _child_bounded_json(unmirrored_child_budget)
    assert len(child_output_before) > parent_budget
    filtered_before = OutputValueFilter(
        max_chars=parent_budget, max_fields=1000, max_recursion=10
    ).filter(child_output_before, tool_name="jira")
    assert filtered_before != child_output_before  # the filter cut it
    with pytest.raises(json.JSONDecodeError):
        json.loads(filtered_before.split("\n\n[OUTPUT TRUNCATED")[0])

    # After: the mirrored value IS the parent's own budget.
    config = _FakeConfig([], max_output_length=parent_budget)
    (cfg,) = _apply_stdio_output_limit_env(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {"command": "x", "args": []},
            }
        ],
        config,
    )
    mirrored_budget = int(cfg["config"]["env"][TOOL_MAX_OUTPUT_LENGTH])
    assert mirrored_budget == parent_budget

    child_output_after = _child_bounded_json(mirrored_budget)
    filtered_after = OutputValueFilter(
        max_chars=parent_budget, max_fields=1000, max_recursion=10
    ).filter(child_output_after, tool_name="jira")
    assert filtered_after == child_output_after
    json.loads(filtered_after)  # still valid JSON, not just unchanged


def test_preexisting_larger_override_would_have_corrupted_json_before_the_overwrite():
    """The exact scenario a reviewer flagged: a config's own env already
    carries a larger, individually-valid output-limit override (e.g. an
    operator- or API-set per-server value) than the parent's effective
    budget. Before this fix preserved such a value; now it's overwritten,
    closing the same JSON-corruption path the rest of this mechanism
    guards against."""
    parent_budget = 120
    preexisting_child_override = 400  # valid on its own, but larger

    config = _FakeConfig([], max_output_length=parent_budget)
    (cfg,) = _apply_stdio_output_limit_env(
        [
            {
                "name": "jira",
                "transport": "stdio",
                "config": {
                    "command": "x",
                    "args": [],
                    "env": {TOOL_MAX_OUTPUT_LENGTH: preexisting_child_override},
                },
            }
        ],
        config,
    )
    mirrored_budget = int(cfg["config"]["env"][TOOL_MAX_OUTPUT_LENGTH])
    assert mirrored_budget == parent_budget  # not the preexisting 400

    # A child that had instead honored its own preexisting override would
    # have produced a valid, larger response the parent's filter still
    # corrupts.
    would_be_child_output = _child_bounded_json(preexisting_child_override)
    assert len(would_be_child_output) > parent_budget
    filtered = OutputValueFilter(
        max_chars=parent_budget, max_fields=1000, max_recursion=10
    ).filter(would_be_child_output, tool_name="jira")
    with pytest.raises(json.JSONDecodeError):
        json.loads(filtered.split("\n\n[OUTPUT TRUNCATED")[0])

    # With the overwritten (mirrored) budget instead, no corruption.
    actual_child_output = _child_bounded_json(mirrored_budget)
    filtered = OutputValueFilter(
        max_chars=parent_budget, max_fields=1000, max_recursion=10
    ).filter(actual_child_output, tool_name="jira")
    assert filtered == actual_child_output
    json.loads(filtered)  # doesn't raise
