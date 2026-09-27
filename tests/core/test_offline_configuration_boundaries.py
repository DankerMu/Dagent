"""Configuration failures must not corrupt valid LAN execution settings."""

import pytest

from xagent import config


def test_invalid_sandbox_environment_entries_do_not_discard_valid_neighbors(
    monkeypatch,
):
    monkeypatch.setenv(
        config.SANDBOX_ENV,
        "MODEL_URL=http://model.internal/v1;=invalid;EMPTY=;malformed;MODE=local",
    )
    assert config.get_sandbox_env() == {
        "MODEL_URL": "http://model.internal/v1",
        "MODE": "local",
    }


def test_empty_mount_components_cannot_become_host_root_mounts(monkeypatch, tmp_path):
    source = tmp_path / "input"
    monkeypatch.setenv(
        config.SANDBOX_VOLUMES,
        f";:/untrusted:rw;{source}::rw;{source}:/input:ro;",
    )
    assert config.get_sandbox_volumes(host_side_sources=True) == [
        (str(source), "/input", "ro")
    ]


@pytest.mark.parametrize("heartbeat", ["not-a-number", "0", "900"])
def test_heartbeat_misconfiguration_keeps_renewal_before_lease_expiry(
    monkeypatch, heartbeat
):
    monkeypatch.setenv(config.TASK_LEASE_TTL_SECONDS, "90")
    monkeypatch.setenv(config.TASK_LEASE_HEARTBEAT_SECONDS, heartbeat)
    interval = config.get_task_lease_heartbeat_seconds()
    ttl = config.get_task_lease_ttl_seconds()
    assert 0 < interval < ttl
    if heartbeat != "900":
        assert interval == ttl // 3


def test_subminimum_lease_rejects_an_unrenewable_interval(monkeypatch):
    monkeypatch.setenv(config.TASK_LEASE_TTL_SECONDS, "0")
    monkeypatch.setenv(config.TASK_LEASE_HEARTBEAT_SECONDS, "900")
    ttl = config.get_task_lease_ttl_seconds()
    assert ttl >= 10
    assert config.get_task_lease_heartbeat_seconds() < ttl
