"""Safety tests for isolated runtime process identity, reset, and proofs."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.engineering import runtime as runtime_cli
from scripts.engineering.runtime_control import (
    ProcessIdentityError,
    allowlisted_env,
    assert_zero_skipped,
    junit_counts,
    process_identity,
    run_proof_pytest,
    terminate_tree,
    verify_process,
)

_HTTP_STANDIN = (
    "import sys\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
    "class H(BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
    "    def log_message(self, *args): pass\n"
    "ThreadingHTTPServer((sys.argv[1], int(sys.argv[2])), H).serve_forever()\n"
)
_TCP_STANDIN = (
    "import socket, sys\n"
    "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind((sys.argv[1], int(sys.argv[2]))); s.listen(1)\n"
    "while True:\n    c,_=s.accept(); c.close()\n"
)
_SIGTERM_IGNORE = (
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
)


def _owned_runtime_root(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    monkeypatch.setattr(runtime_cli, "REPO_ROOT", repo)
    root = repo / ".run" / "runtime"
    root.mkdir(parents=True)
    (root / "OWNED_BY_DAGENT_RUNTIME").write_text(runtime_cli.OWNERSHIP_NAME + "\n")
    (root / "db").mkdir()
    (root / "state").mkdir()
    return root


def _write_owned_state(root: Path, **extra: object) -> dict:
    payload = {
        "owner": runtime_cli.OWNERSHIP_NAME,
        "root": str(root),
        "web": None,
        "redis": None,
        "database_url": runtime_cli._owned_database_url(root),
        **extra,
    }
    runtime_cli._write_state(root, payload)
    return payload


def _popen_standin(original, argv, **kwargs):
    command = [str(item) for item in argv]
    if command and Path(command[0]).name == "redis-server":
        port = command[command.index("--port") + 1]
        return original(
            [sys.executable, "-c", _TCP_STANDIN, "127.0.0.1", port],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            cwd=kwargs.get("cwd"),
            start_new_session=True,
        )
    if "-m" in command and "xagent.web" in command:
        return original(
            [
                sys.executable,
                "-c",
                _HTTP_STANDIN,
                command[command.index("--host") + 1],
                command[command.index("--port") + 1],
            ],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            cwd=kwargs.get("cwd"),
            env=kwargs.get("env"),
            start_new_session=True,
        )
    return original(argv, **kwargs)


def _install_runtime_standins(monkeypatch) -> None:
    original = subprocess.Popen

    def _wrapped(argv, **kwargs):
        return _popen_standin(original, argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _wrapped)
    monkeypatch.setattr(runtime_cli.subprocess, "Popen", _wrapped)
    monkeypatch.setattr(
        runtime_cli.shutil,
        "which",
        lambda name: "/usr/bin/redis-server" if name == "redis-server" else None,
    )


def _capture_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _assert_fail(capsys, action, needle: str) -> None:
    with pytest.raises(SystemExit) as rejected:
        action()
    assert rejected.value.code == 1
    assert needle in capsys.readouterr().err


def _sleeping_child():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )


def test_verify_process_refuses_reused_pid() -> None:
    child = _sleeping_child()
    try:
        identity = process_identity(child.pid)
        reused = dict(identity)
        reused["create_time"] = identity["create_time"] - 10
        with pytest.raises(ProcessIdentityError, match="create_time"):
            verify_process(reused)
        with pytest.raises(ProcessIdentityError, match="missing process identity"):
            verify_process(None)
        mismatched = dict(identity)
        mismatched["exe"] = str(Path(identity["exe"]).with_name("not-the-owned-exe"))
        with pytest.raises(ProcessIdentityError, match="executable mismatch"):
            verify_process(mismatched)
        assert verify_process(identity).pid == child.pid
    finally:
        child.kill()
        child.wait(timeout=5)
    with pytest.raises(ProcessIdentityError, match="does not exist"):
        verify_process(identity)


def test_assert_zero_skipped_rejects_empty_and_skipped() -> None:
    with pytest.raises(SystemExit, match="zero tests"):
        assert_zero_skipped(
            {"tests": 0, "executed": 0, "failures": 0, "errors": 0, "skipped": 0}
        )
    with pytest.raises(SystemExit, match="skipped tests"):
        assert_zero_skipped(
            {"tests": 2, "executed": 1, "failures": 0, "errors": 0, "skipped": 1}
        )
    with pytest.raises(SystemExit, match="failures or errors"):
        assert_zero_skipped(
            {"tests": 1, "executed": 1, "failures": 1, "errors": 0, "skipped": 0}
        )
    assert_zero_skipped(
        {"tests": 3, "executed": 3, "failures": 0, "errors": 0, "skipped": 0}
    )


def test_junit_counts_suite_missing_and_testsuites(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite tests="4" failures="0" errors="0" skipped="1"></testsuite>',
        encoding="utf-8",
    )
    assert junit_counts(report) == {
        "tests": 4,
        "executed": 3,
        "failures": 0,
        "errors": 0,
        "skipped": 1,
    }
    with pytest.raises(ValueError, match="missing junit report"):
        junit_counts(tmp_path / "absent.xml")
    empty = tmp_path / "empty.xml"
    empty.write_text(
        '<not-junit><suite tests="2" failures="0" errors="0" skipped="0"/></not-junit>',
        encoding="utf-8",
    )
    assert junit_counts(empty)["tests"] == 0
    wrapped = tmp_path / "wrapped.xml"
    wrapped.write_text(
        '<testsuites name="proof"><other tests="9" failures="0" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    counts = junit_counts(wrapped)
    assert counts["tests"] == 9 and counts["executed"] == 9


def test_reset_refuses_repo_home_unmarked_and_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(runtime_cli, "REPO_ROOT", tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    for protected in (tmp_path / "repo", Path.home(), Path.home() / ".xagent"):
        with pytest.raises(SystemExit) as rejected:
            runtime_cli.cmd_reset(protected)
        assert rejected.value.code == 1
    unmarked = tmp_path / "repo" / ".run" / "runtime"
    unmarked.mkdir(parents=True)
    sentinel = unmarked / "user-data"
    sentinel.write_text("preserve")
    with pytest.raises(SystemExit) as rejected:
        runtime_cli.cmd_reset(unmarked)
    assert rejected.value.code == 1
    assert sentinel.read_text() == "preserve"
    real = unmarked
    (real / "OWNED_BY_DAGENT_RUNTIME").write_text(runtime_cli.OWNERSHIP_NAME + "\n")
    link = tmp_path / "alias"
    link.symlink_to(real)
    with pytest.raises(SystemExit) as rejected:
        runtime_cli.cmd_reset(link)
    assert rejected.value.code == 1
    assert link.is_symlink()
    assert (real / "OWNED_BY_DAGENT_RUNTIME").is_file()


def test_seed_refuses_foreign_and_symlinked_database(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    with pytest.raises(SystemExit) as rejected:
        runtime_cli._seed(tmp_path, {"database_url": "sqlite:////tmp/not-owned.db"})
    assert rejected.value.code == 1
    root = _owned_runtime_root(tmp_path, monkeypatch)
    real_db = tmp_path / "elsewhere.db"
    real_db.write_text("foreign", encoding="utf-8")
    link = root / "db" / "runtime.db"
    link.symlink_to(real_db)
    with pytest.raises(SystemExit) as rejected:
        runtime_cli._seed(root, {"database_url": f"sqlite:///{link}"})
    assert rejected.value.code == 1
    assert real_db.read_text() == "foreign"
    link.unlink()
    (root / "db").rmdir()
    (tmp_path / "foreign-db-dir").mkdir()
    (tmp_path / "foreign-db-dir" / "runtime.db").write_text("foreign", encoding="utf-8")
    (root / "db").symlink_to(tmp_path / "foreign-db-dir")
    _assert_fail(
        capsys,
        lambda: runtime_cli._seed(
            root, {"database_url": runtime_cli._owned_database_url(root)}
        ),
        "symlinked",
    )


def test_stopped_stack_can_be_reset(tmp_path: Path, monkeypatch) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    db = root / "db" / "runtime.db"
    db.write_text("stale", encoding="utf-8")
    _write_owned_state(root, database_url=f"sqlite:///{db.resolve()}")
    assert runtime_cli.cmd_reset(root) == 0
    assert not db.exists()
    assert (root / "OWNED_BY_DAGENT_RUNTIME").is_file()


def test_owned_root_refuses_home_outside_run_marker_and_fresh_require(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(runtime_cli, "REPO_ROOT", repo)
    nested = tmp_path / "home" / ".xagent" / "nested"
    nested.mkdir(parents=True)
    monkeypatch.setattr(
        runtime_cli.Path, "home", classmethod(lambda cls: tmp_path / "home")
    )
    _assert_fail(capsys, lambda: runtime_cli._owned_root(nested), "~/.xagent")
    outside = repo / "src"
    outside.mkdir()
    _assert_fail(capsys, lambda: runtime_cli._owned_root(outside), "outside")
    run_root = repo / ".run"
    run_root.mkdir()
    _assert_fail(
        capsys, lambda: runtime_cli._owned_root(run_root), "Refusing runtime root"
    )
    root = _owned_runtime_root(tmp_path, monkeypatch)
    marker = root / "OWNED_BY_DAGENT_RUNTIME"
    marker.unlink()
    target = tmp_path / "marker-target"
    target.write_text("not-owned", encoding="utf-8")
    marker.symlink_to(target)
    _assert_fail(
        capsys, lambda: runtime_cli._owned_root(root), "symlinked ownership marker"
    )
    fresh = root.parent / "fresh-runtime"
    assert runtime_cli._owned_root(fresh) == fresh.resolve()
    _assert_fail(
        capsys, lambda: runtime_cli._require_owned_root(fresh), "not owned by this CLI"
    )
    assert not fresh.exists()


def test_load_state_rejects_corrupt_and_foreign_owner(
    tmp_path: Path, monkeypatch
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    path = runtime_cli._state_path(root)
    path.write_text("{not-json", encoding="utf-8")
    assert runtime_cli._load_state(root) is None
    path.write_text(json.dumps({"owner": "someone-else", "root": str(root)}))
    assert runtime_cli._load_state(root) is None


def test_child_env_isolates_home_and_database(tmp_path: Path, monkeypatch) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.delenv("XAGENT_FRONTEND_DIST_DIR", raising=False)
    frontend = tmp_path / "repo" / "frontend" / "out"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    env = runtime_cli._child_env(root, "redis://127.0.0.1:9/0")
    assert env["HOME"] == str(root / "home")
    assert env["DATABASE_URL"] == f"sqlite:///{root / 'db' / 'runtime.db'}"
    assert env["XAGENT_REDIS_URL"] == "redis://127.0.0.1:9/0"
    assert env["XAGENT_FRONTEND_DIST_DIR"] == str(frontend)
    monkeypatch.setenv("XAGENT_FRONTEND_DIST_DIR", "/custom/frontend")
    assert (
        runtime_cli._child_env(root, "redis://127.0.0.1:9/0")[
            "XAGENT_FRONTEND_DIST_DIR"
        ]
        == "/custom/frontend"
    )


def test_health_reports_ready_and_connection_failure() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert runtime_cli._health(f"http://127.0.0.1:{server.server_address[1]}") == (
            True,
            True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert runtime_cli._health("http://127.0.0.1:1") == (False, False)


def test_start_redis_configured_missing_hung_and_exited(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    (root / "logs").mkdir(exist_ok=True)
    monkeypatch.setenv("XAGENT_TEST_REDIS_URL", "redis://configured:6379/0")
    identity, url = runtime_cli._start_redis(root)
    assert identity is None and url == "redis://configured:6379/0"
    monkeypatch.delenv("XAGENT_TEST_REDIS_URL")
    monkeypatch.setattr(runtime_cli.shutil, "which", lambda name: None)
    _assert_fail(capsys, lambda: runtime_cli._start_redis(root), "redis-server")
    original = subprocess.Popen
    monkeypatch.setattr(
        runtime_cli.shutil, "which", lambda name: "/usr/bin/redis-server"
    )

    def _hung(argv, **kwargs):
        del argv
        return original(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            cwd=kwargs.get("cwd"),
            start_new_session=True,
        )

    monkeypatch.setattr(runtime_cli, "REDIS_READY_SECONDS", 0.2)
    monkeypatch.setattr(runtime_cli.subprocess, "Popen", _hung)
    _assert_fail(capsys, lambda: runtime_cli._start_redis(root), "did not become ready")

    def _exits(argv, **kwargs):
        del argv
        return original(
            [sys.executable, "-c", "raise SystemExit(9)"],
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            cwd=kwargs.get("cwd"),
            start_new_session=True,
        )

    monkeypatch.setattr(runtime_cli.subprocess, "Popen", _exits)
    _assert_fail(capsys, lambda: runtime_cli._start_redis(root), "exited 9")


def test_cmd_status_stop_and_logs(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    assert runtime_cli.cmd_status(root) == 1
    assert _capture_json(capsys)["status"] == "stopped"
    _write_owned_state(root)
    assert runtime_cli.cmd_status(root) == 1
    assert _capture_json(capsys)["status"] == "stopped"
    child = _sleeping_child()
    try:
        _write_owned_state(
            root, web=process_identity(child.pid), base_url="http://127.0.0.1:1"
        )
        assert runtime_cli.cmd_status(root) == 1
        stale = _capture_json(capsys)
        assert stale["status"] == "stale" and stale["process_ok"] is True
        dead = dict(process_identity(child.pid))
        child.kill()
        child.wait(timeout=5)
        _write_owned_state(root, web=dead, base_url="http://127.0.0.1:1")
        assert runtime_cli.cmd_status(root) == 1
        missing = _capture_json(capsys)
        assert missing["status"] == "stale" and missing["process_ok"] is False
        assert stale["health"] is False
        logs = root / "logs"
        logs.mkdir()
        (logs / "xagent.log").write_text("web-log", encoding="utf-8")
        assert runtime_cli.cmd_stop(root) == 0
        assert _capture_json(capsys)["status"] == "stopped"
        state = runtime_cli._load_state(root)
        assert state is not None and state["web"] is None
    finally:
        child.wait(timeout=5)
    assert runtime_cli.cmd_logs(root) == 0
    logged = capsys.readouterr().out
    assert "xagent.log" in logged and "web-log" in logged


def test_cmd_seed_refuses_unstarted_then_inserts_owner(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    _assert_fail(capsys, lambda: runtime_cli.cmd_seed(root), "not started")
    _write_owned_state(root)
    assert runtime_cli.cmd_seed(root) == 0
    assert _capture_json(capsys) == {
        "status": "seeded",
        "username": runtime_cli.PROOF_OWNER_USERNAME,
    }
    db_path = root / "db" / "runtime.db"
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT username, is_admin FROM users WHERE username = ?",
            (runtime_cli.PROOF_OWNER_USERNAME,),
        ).fetchall()
    assert rows == [(runtime_cli.PROOF_OWNER_USERNAME, 1)]
    assert runtime_cli.cmd_seed(root) == 0
    with sqlite3.connect(db_path) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM users WHERE username = ?",
            (runtime_cli.PROOF_OWNER_USERNAME,),
        ).fetchone()
    assert count == (1,)


def test_reset_refuses_running_foreign_url_and_symlink_paths(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    child = _sleeping_child()
    try:
        _write_owned_state(root, web=process_identity(child.pid))
        _assert_fail(capsys, lambda: runtime_cli.cmd_reset(root), "running stack")
    finally:
        child.kill()
        child.wait(timeout=5)
    _write_owned_state(root, web=None)
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/foreign-reset.db")
    _assert_fail(capsys, lambda: runtime_cli.cmd_reset(root), "arbitrary DATABASE_URL")
    monkeypatch.delenv("DATABASE_URL")
    (tmp_path / "elsewhere-uploads").mkdir()
    (root / "uploads").symlink_to(tmp_path / "elsewhere-uploads")
    _assert_fail(capsys, lambda: runtime_cli.cmd_reset(root), "symlinked path")


def test_rmtree_unlinks_file_and_leaves_missing_path(tmp_path: Path) -> None:
    file_path = tmp_path / "plain-file"
    file_path.write_text("gone", encoding="utf-8")
    runtime_cli._rmtree(file_path)
    assert not file_path.exists()
    runtime_cli._rmtree(tmp_path / "missing")


def test_proof_paths_refuse_existing_unmarked_root_and_mark_fresh(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.setattr(runtime_cli, "REPO_ROOT", repo)
    unmarked = repo / ".run" / "runtime"
    unmarked.mkdir(parents=True)
    sentinel = unmarked / "user-data"
    sentinel.write_text("preserve", encoding="utf-8")
    _assert_fail(
        capsys, lambda: runtime_cli._proof_paths(unmarked, "smoke"), "unmarked"
    )
    assert sentinel.read_text() == "preserve"
    assert not (unmarked / "OWNED_BY_DAGENT_RUNTIME").exists()
    _assert_fail(capsys, lambda: runtime_cli.cmd_reset(unmarked), "unmarked")
    assert sentinel.read_text() == "preserve"
    planted = repo / ".run" / "planted"
    planted.mkdir(parents=True)
    planted_sentinel = planted / "user-data"
    planted_sentinel.write_text("preserve-planted", encoding="utf-8")
    runtime_cli._write_state(
        planted,
        {
            "owner": runtime_cli.OWNERSHIP_NAME,
            "root": str(planted),
            "web": None,
            "redis": None,
        },
    )
    assert not (planted / "OWNED_BY_DAGENT_RUNTIME").exists()
    _assert_fail(capsys, lambda: runtime_cli._proof_paths(planted, "smoke"), "unmarked")
    assert planted_sentinel.read_text() == "preserve-planted"
    assert not (planted / "OWNED_BY_DAGENT_RUNTIME").exists()
    _assert_fail(capsys, lambda: runtime_cli.cmd_reset(planted), "not owned")
    assert planted_sentinel.read_text() == "preserve-planted"
    fresh = repo / ".run" / "fresh-proof"
    basetemp, junit = runtime_cli._proof_paths(fresh, "smoke")
    assert (fresh / "OWNED_BY_DAGENT_RUNTIME").read_text() == (
        runtime_cli.OWNERSHIP_NAME + "\n"
    )
    assert basetemp == fresh / "proofs" / "smoke"
    assert junit == fresh / "proofs" / "smoke.xml"
    again_temp, again_junit = runtime_cli._proof_paths(fresh, "ui")
    assert again_temp == fresh / "proofs" / "ui"
    assert again_junit == fresh / "proofs" / "ui.xml"


def test_proof_commands_refuse_prerequisites_and_forward_env(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    monkeypatch.delenv("XAGENT_FRONTEND_DIST_DIR", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    _assert_fail(
        capsys, lambda: runtime_cli.cmd_ui(root), "Frontend static export is missing"
    )
    _assert_fail(capsys, lambda: runtime_cli.cmd_real_model(root), "OPENAI_BASE_URL")
    captured: dict[str, object] = {}

    def _capture(args, *, extra_env, basetemp, junit_path, cwd):
        captured.update(args=list(args), extra_env=dict(extra_env), cwd=cwd)
        return 0

    monkeypatch.setattr(runtime_cli, "run_proof_pytest", _capture)
    monkeypatch.setenv("XAGENT_FRONTEND_DIST_DIR", "/frontend-dist")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/pw-browsers")
    monkeypatch.setenv("OPENAI_API_KEY", "live-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://model.internal/v1")
    monkeypatch.setenv("OPENAI_MODEL", "local-model")
    assert runtime_cli.cmd_ui(root) == 0
    assert captured["extra_env"] == {
        "XAGENT_RUNTIME_PROOF": "ui",
        "PLAYWRIGHT_BROWSERS_PATH": "/pw-browsers",
        "XAGENT_FRONTEND_DIST_DIR": "/frontend-dist",
    }
    assert captured["cwd"] == runtime_cli.REPO_ROOT
    assert runtime_cli.cmd_real_model(root) == 0
    assert captured["args"] == [
        "tests/e2e/test_model_live.py",
        "--run-special",
        "--real-model",
    ]
    assert (
        captured["extra_env"]
        == {
            "XAGENT_RUNTIME_PROOF": "real-model",
            "OPENAI_API_KEY": "live-key",  # pragma: allowlist secret - synthetic forwarding sentinel
            "OPENAI_BASE_URL": "http://model.internal/v1",
            "OPENAI_MODEL": "local-model",
        }
    )
    assert runtime_cli.cmd_smoke(root) == 0
    assert captured["args"] == ["tests/e2e/test_api_smoke.py", "--run-special"]
    assert captured["extra_env"] == {"XAGENT_RUNTIME_PROOF": "smoke"}


def test_allowlisted_env_drops_secrets_and_keeps_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/unsafe-home")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/foreign.db")
    env = allowlisted_env({"XAGENT_RUNTIME_PROOF": "smoke"})
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/unsafe-home"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["LANGFUSE_TRACING_ENABLED"] == "false"
    assert env["XAGENT_RUNTIME_PROOF"] == "smoke"
    assert "OPENAI_API_KEY" not in env
    assert "DATABASE_URL" not in env


def test_run_proof_pytest_missing_skip_blocked_pass_and_timeout(tmp_path: Path) -> None:
    cwd = tmp_path / "suite"
    cwd.mkdir()
    (cwd / "pass_test.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (cwd / "skip_test.py").write_text(
        "import pytest\n\n"
        "def test_skip():\n    pytest.skip('missing credential')\n"
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    (cwd / "slow_test.py").write_text(
        "import time\n\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="zero tests"):
        run_proof_pytest(
            ["pass_test.py", "--collect-only"],
            extra_env={},
            basetemp=tmp_path / "base-missing",
            junit_path=tmp_path / "missing-parent" / "junit.xml",
            cwd=cwd,
        )
    with pytest.raises(SystemExit, match="skipped tests"):
        run_proof_pytest(
            ["skip_test.py"],
            extra_env={},
            basetemp=tmp_path / "base-skip",
            junit_path=tmp_path / "skip.xml",
            cwd=cwd,
        )
    blocked = tmp_path / "blocked-junit"
    blocked.mkdir()
    with pytest.raises(SystemExit, match="did not write a JUnit report"):
        run_proof_pytest(
            ["pass_test.py"],
            extra_env={},
            basetemp=tmp_path / "base-blocked",
            junit_path=blocked,
            cwd=cwd,
        )
    code = run_proof_pytest(
        ["pass_test.py"],
        extra_env={"XAGENT_RUNTIME_PROOF": "unit"},
        basetemp=tmp_path / "base-pass",
        junit_path=tmp_path / "pass.xml",
        cwd=cwd,
    )
    assert code == 0
    counts = junit_counts(tmp_path / "pass.xml")
    assert counts["executed"] == 1 and counts["skipped"] == 0
    with pytest.raises(SystemExit, match="timed out"):
        run_proof_pytest(
            ["slow_test.py"],
            extra_env={},
            basetemp=tmp_path / "base-slow",
            junit_path=tmp_path / "slow.xml",
            cwd=cwd,
            timeout=0.2,
        )


def test_terminate_tree_ignores_missing_and_kills_stubborn_child() -> None:
    terminate_tree(None)
    terminate_tree({"pid": 1, "create_time": 0.0, "exe": "/no-such-exe"})
    child = subprocess.Popen(
        [sys.executable, "-c", _SIGTERM_IGNORE], start_new_session=True
    )
    terminate_tree(process_identity(child.pid), timeout=0.2)
    child.wait(timeout=5)
    assert child.returncode is not None


def test_cmd_start_status_stop_lifecycle(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    _install_runtime_standins(monkeypatch)
    monkeypatch.delenv("XAGENT_TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", "test-encryption-key")
    assert runtime_cli.cmd_start(root) == 0
    started = _capture_json(capsys)
    assert started["owner"] == runtime_cli.OWNERSHIP_NAME
    assert started["root"] == str(root)
    assert started["database_url"] == runtime_cli._owned_database_url(root)
    assert started["web"]["pid"]
    assert runtime_cli.cmd_start(root) == 0
    assert _capture_json(capsys)["status"] == "already-running"
    assert runtime_cli.cmd_status(root) == 0
    status = _capture_json(capsys)
    assert status["status"] == "running" and status["process_ok"] is True
    assert runtime_cli.cmd_stop(root) == 0
    assert _capture_json(capsys)["status"] == "stopped"
    state = runtime_cli._load_state(root)
    assert state is not None and state["web"] is None and state["redis"] is None
    assert runtime_cli.main(["--root", str(root), "start"]) == 0
    started_again = _capture_json(capsys)
    assert started_again["owner"] == runtime_cli.OWNERSHIP_NAME
    assert started_again["web"]["pid"]
    assert runtime_cli.cmd_stop(root) == 0


def test_cmd_start_cleans_redis_when_web_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    _install_runtime_standins(monkeypatch)
    monkeypatch.delenv("XAGENT_TEST_REDIS_URL", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", "test-encryption-key")
    monkeypatch.setattr(runtime_cli, "STARTUP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(runtime_cli, "_health", lambda base_url: (False, False))
    _assert_fail(capsys, lambda: runtime_cli.cmd_start(root), "did not become ready")
    assert runtime_cli._load_state(root) is None


def test_main_status_stop_logs_seed_reset_and_refusals(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _owned_runtime_root(tmp_path, monkeypatch)
    logs = root / "logs"
    logs.mkdir()
    (logs / "xagent.log").write_text("from-main", encoding="utf-8")
    _write_owned_state(root)
    assert runtime_cli.main(["--root", str(root), "status"]) == 1
    assert _capture_json(capsys)["status"] == "stopped"
    assert runtime_cli.main(["--root", str(root), "stop"]) == 0
    assert _capture_json(capsys)["status"] == "stopped"
    assert runtime_cli.main(["--root", str(root), "logs"]) == 0
    assert "from-main" in capsys.readouterr().out
    assert runtime_cli.main(["--root", str(root), "seed"]) == 0
    assert _capture_json(capsys)["status"] == "seeded"
    assert runtime_cli.main(["--root", str(root), "reset"]) == 0
    assert _capture_json(capsys)["status"] == "reset"
    with pytest.raises(SystemExit) as exited:
        runtime_cli.main(["--root", str(root), "not-a-command"])
    assert exited.value.code == 2
    with pytest.raises(SystemExit) as helped:
        runtime_cli.main(["--help"])
    assert helped.value.code == 0
    monkeypatch.delenv("XAGENT_FRONTEND_DIST_DIR", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    _assert_fail(
        capsys,
        lambda: runtime_cli.main(["--root", str(root), "ui"]),
        "Frontend static export is missing",
    )
    _assert_fail(
        capsys,
        lambda: runtime_cli.main(["--root", str(root), "real-model"]),
        "OPENAI_BASE_URL",
    )
    captured: dict[str, object] = {}

    def _capture(args, *, extra_env, basetemp, junit_path, cwd):
        captured.update(args=list(args), extra_env=dict(extra_env))
        return 0

    monkeypatch.setattr(runtime_cli, "run_proof_pytest", _capture)
    assert runtime_cli.main(["--root", str(root), "smoke"]) == 0
    assert captured["args"] == ["tests/e2e/test_api_smoke.py", "--run-special"]
    assert captured["extra_env"] == {"XAGENT_RUNTIME_PROOF": "smoke"}


def test_direct_runtime_cli_help_runs_without_package() -> None:
    completed = subprocess.run(
        [sys.executable, str(Path(runtime_cli.__file__)), "--help"],
        cwd=Path(runtime_cli.__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "start" in completed.stdout and "real-model" in completed.stdout
    assert "ImportError" not in completed.stderr
