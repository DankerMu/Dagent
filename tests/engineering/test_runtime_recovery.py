"""A degraded owned stack must never be silently replaced or reset."""

import pytest

from scripts.engineering import runtime as runtime_cli
from scripts.engineering.runtime_control import process_identity, terminate_tree
from tests.engineering.test_runtime import (
    _install_runtime_standins,
    _owned_runtime_root,
    _sleeping_child,
    _write_owned_state,
)


@pytest.mark.parametrize("component", ["web", "redis"])
def test_start_refuses_to_abandon_a_live_degraded_stack(
    tmp_path, monkeypatch, component
):
    root = _owned_runtime_root(tmp_path, monkeypatch)
    _install_runtime_standins(monkeypatch)
    monkeypatch.delenv("XAGENT_TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", "test-encryption-key")
    child = _sleeping_child()
    identity = process_identity(child.pid)
    original = _write_owned_state(root, **{component: identity})
    try:
        with pytest.raises(SystemExit):
            runtime_cli.cmd_start(root)
        assert child.poll() is None
        assert runtime_cli._load_state(root) == original
    finally:
        runtime_cli.cmd_stop(root)
        terminate_tree(identity)
        child.wait(timeout=5)


@pytest.mark.parametrize("component", ["web", "redis"])
def test_reset_refuses_any_live_owned_component(tmp_path, monkeypatch, component):
    root = _owned_runtime_root(tmp_path, monkeypatch)
    child = _sleeping_child()
    identity = process_identity(child.pid)
    _write_owned_state(root, **{component: identity})
    data = root / "db" / "sentinel"
    data.write_text("must survive")
    try:
        with pytest.raises(SystemExit):
            runtime_cli.cmd_reset(root)
        assert child.poll() is None
        assert data.read_text() == "must survive"
    finally:
        terminate_tree(identity)
        child.wait(timeout=5)
