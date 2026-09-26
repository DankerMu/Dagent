"""Unreadable process metadata must not identify a Chrome daemon."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from xagent.core.tools.adapters.vibe.sandboxed_tool import chrome_daemon_runner


def test_unreadable_process_cmdline_must_not_identify_a_daemon(tmp_path, monkeypatch):
    """Terminate refuses to signal a pid whose /proc cmdline cannot be read.

    Local macOS always hits OSError for /proc/<pid>/cmdline; Linux CI can
    read it for the current process and therefore never takes the fail-closed
    branch. Patching Path.read_bytes to PermissionError makes that branch
    deterministic on both platforms without claiming an unverified pid is ours.
    """
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242", encoding="utf-8")

    real_read_bytes = Path.read_bytes

    def _unreadable_cmdline(self: Path) -> bytes:
        if self.name == "cmdline" and self.parent.name == "4242":
            raise PermissionError("proc cmdline is unreadable")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _unreadable_cmdline)

    with patch.object(chrome_daemon_runner, "_kill_process") as kill:
        with pytest.raises(
            chrome_daemon_runner.ChromeDaemonRunnerError, match="unverified"
        ):
            chrome_daemon_runner._terminate_expected_daemon(pid_file)

    kill.assert_not_called()
