#!/usr/bin/env python3
"""Isolated runtime lifecycle and proof orchestration for Dagent engineering gates.

Subcommands: start, stop, status, logs, seed, reset, smoke, ui, real-model.

The stack owns a unique isolated root, never ~/.xagent and never an arbitrary
DATABASE_URL. Child hosts receive an allowlisted environment. Proof pytest
invocations fail when selected tests are missing or skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    from scripts.engineering.runtime_control import (
        ProcessIdentityError,
        allowlisted_env,
        process_identity,
        run_proof_pytest,
        terminate_tree,
        verify_process,
    )
except ModuleNotFoundError:  # python scripts/engineering/runtime.py
    from runtime_control import (  # type: ignore[no-redef]
        ProcessIdentityError,
        allowlisted_env,
        process_identity,
        run_proof_pytest,
        terminate_tree,
        verify_process,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path(
    os.environ.get("XAGENT_RUNTIME_ROOT") or (REPO_ROOT / ".run" / "runtime")
)
OWNERSHIP_NAME = "dagent-runtime-proof"
STARTUP_TIMEOUT_SECONDS = 60
REDIS_READY_SECONDS = 10
DEFAULT_DMX_BASE_URL = "https://www.dmxapi.cn/v1"
PROOF_OWNER_USERNAME = "e2e-owner"
# Fixed credential for the disposable loopback-only proof database.
# nosemgrep: dagent.hardcoded-password-assign
PROOF_OWNER_PASSWORD = "e2e-owner-password"  # pragma: allowlist secret


def _fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _state_dir(root: Path) -> Path:
    return root / "state"


def _state_path(root: Path) -> Path:
    return _state_dir(root) / "runtime.json"


def _load_state(root: Path) -> dict | None:
    path = _state_path(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if payload.get("owner") != OWNERSHIP_NAME:
        return None
    return payload


def _write_state(root: Path, payload: dict) -> None:
    directory = _state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = _state_path(root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def _owned_database_url(root: Path) -> str:
    return f"sqlite:///{(root / 'db' / 'runtime.db').resolve()}"


def _owned_database_path(root: Path) -> Path:
    db_dir = root / "db"
    db_file = db_dir / "runtime.db"
    if db_dir.is_symlink() or db_file.is_symlink():
        _fail("Refuse to use a symlinked database path")
    resolved = db_file.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        _fail("Refuse to use a database path outside the owned root")
    return resolved


def _health(base_url: str) -> tuple[bool, bool]:
    try:
        import httpx

        health = httpx.get(f"{base_url}/health", timeout=1)
        ready = httpx.get(f"{base_url}/ready", timeout=1)
        return health.status_code == 200, ready.status_code == 200
    except Exception:
        return False, False


def _owned_root(root: Path) -> Path:
    requested = root.expanduser()
    resolved = requested.resolve()
    run_root = (REPO_ROOT / ".run").resolve()
    home = Path.home().resolve()
    if requested.is_symlink() or resolved.is_symlink():
        _fail(f"Refusing symlinked runtime root {requested}")
    forbidden = {home, home / ".xagent", REPO_ROOT, run_root, Path("/")}
    if resolved in forbidden:
        _fail(f"Refusing runtime root {resolved}")
    try:
        resolved.relative_to(home / ".xagent")
        _fail("Refusing to use ~/.xagent as the runtime root")
    except ValueError:
        pass
    try:
        resolved.relative_to(REPO_ROOT)
        try:
            resolved.relative_to(run_root)
        except ValueError:
            _fail(
                f"Refusing runtime root inside the repo but outside {run_root}: {resolved}"
            )
        if resolved == run_root:
            _fail(f"Refusing to use {run_root} itself as the runtime root")
    except ValueError:
        pass
    marker = resolved / "OWNED_BY_DAGENT_RUNTIME"
    if marker.is_symlink():
        _fail(f"Refusing symlinked ownership marker at {resolved}")
    state = _load_state(resolved)
    if resolved.exists() and not marker.is_file() and state is None:
        _fail(f"Refusing unmarked directory {resolved}")
    return resolved


def _require_owned_root(root: Path) -> Path:
    resolved = _owned_root(root)
    marker = resolved / "OWNED_BY_DAGENT_RUNTIME"
    if not marker.is_file():
        _fail(f"Runtime root is not owned by this CLI: {resolved}")
    return resolved


def _claim_fresh_or_owned_root(root: Path) -> Path:
    owned = _owned_root(root)
    marker = owned / "OWNED_BY_DAGENT_RUNTIME"
    if owned.exists():
        if not marker.is_file():
            _fail(f"Refusing unmarked directory {owned}")
        return owned
    owned.mkdir(parents=True, exist_ok=True)
    marker.write_text(OWNERSHIP_NAME + "\n")
    return owned


def _child_env(root: Path, redis_url: str) -> dict[str, str]:
    from cryptography.fernet import Fernet

    environment = allowlisted_env()
    encryption_key = os.environ.get("ENCRYPTION_KEY") or Fernet.generate_key().decode()
    environment.update(
        {
            "HOME": str(root / "home"),
            "DATABASE_URL": f"sqlite:///{root / 'db' / 'runtime.db'}",
            "XAGENT_UPLOADS_DIR": str(root / "uploads"),
            "XAGENT_FILE_MATERIALIZE_DIR": str(root / "materialized"),
            "XAGENT_STORAGE_ROOT": str(root / "storage"),
            "LANCEDB_DIR": str(root / "lancedb"),
            "LANCEDB_PATH": str(root / "lancedb-path"),
            "LANCEDB_AUTO_MIGRATE": "false",
            "LANGFUSE_TRACING_ENABLED": "false",
            "ENVIRONMENT": "development",
            "XAGENT_CELERY_ENABLED": "false",
            "XAGENT_CHANNEL_INGRESS_ENABLED": "false",
            "XAGENT_SHARED_TASK_EXECUTION_ENABLED": "true",
            "XAGENT_TASK_EXECUTION_ROLE": "combined",
            "XAGENT_FILE_STORAGE_STARTUP_SYNC_ENABLED": "false",
            "XAGENT_REDIS_URL": redis_url,
            "ENCRYPTION_KEY": encryption_key,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    frontend = os.environ.get("XAGENT_FRONTEND_DIST_DIR")
    if frontend:
        environment["XAGENT_FRONTEND_DIST_DIR"] = frontend
    else:
        default_frontend = REPO_ROOT / "frontend" / "out"
        if (default_frontend / "index.html").is_file():
            environment["XAGENT_FRONTEND_DIST_DIR"] = str(default_frontend)
    return environment


def _start_redis(root: Path) -> tuple[dict | None, str]:
    configured = os.environ.get("XAGENT_TEST_REDIS_URL")
    if configured:
        return None, configured
    executable = shutil.which("redis-server")
    if executable is None:
        _fail("E2E runtime needs XAGENT_TEST_REDIS_URL or a local redis-server")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log = (root / "logs" / "redis.log").open("w")
    process = subprocess.Popen(
        [
            executable,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(root),
        start_new_session=True,
    )
    identity = process_identity(process.pid)
    url = f"redis://127.0.0.1:{port}/0"
    deadline = time.monotonic() + REDIS_READY_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _fail(f"redis-server exited {process.returncode}")
        with socket.socket() as probe:
            probe.settimeout(0.2)
            try:
                probe.connect(("127.0.0.1", port))
                return identity, url
            except OSError:
                time.sleep(0.05)
    terminate_tree(identity)
    _fail("redis-server did not become ready")
    raise AssertionError


def _start_xagent(root: Path, environment: dict[str, str], port: int) -> dict:
    log = (root / "logs" / "xagent.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "xagent.web",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        env=environment,
        start_new_session=True,
    )
    identity = process_identity(process.pid)
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _fail(
                f"xagent web exited {process.returncode} before ready\n"
                f"{(root / 'logs' / 'xagent.log').read_text()[-4000:]}"
            )
        healthy, ready = _health(f"http://127.0.0.1:{port}")
        if healthy and ready:
            return identity
        time.sleep(0.2)
    terminate_tree(identity)
    _fail("xagent web did not become ready")
    raise AssertionError


def _allocate_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _verified_live(identity: dict | None) -> bool:
    try:
        verify_process(identity)
    except ProcessIdentityError:
        return False
    return True


def _refuse_live_owned(web_live: bool, redis_live: bool, action: str) -> None:
    live = [
        name for name, present in (("web", web_live), ("redis", redis_live)) if present
    ]
    if not live:
        return
    named = " and ".join(live)
    _fail(f"Refuse to {action} a running stack with live {named}; stop it first")


def cmd_start(root: Path) -> int:
    owned = _owned_root(root)
    existing = _load_state(owned)
    if existing:
        healthy, ready = _health(str(existing.get("base_url") or ""))
        web_live = _verified_live(existing.get("web"))
        redis_live = _verified_live(existing.get("redis"))
        if web_live and healthy and ready:
            print(
                json.dumps({"status": "already-running", "root": str(owned)}, indent=2)
            )
            return 0
        _refuse_live_owned(web_live, redis_live, "replace")
    for path in (
        owned / "home",
        owned / "db",
        owned / "uploads",
        owned / "storage",
        owned / "logs",
        owned / "lancedb",
        owned / "proofs",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (owned / "OWNED_BY_DAGENT_RUNTIME").write_text(OWNERSHIP_NAME + "\n")
    redis_identity, redis_url = _start_redis(owned)
    environment = _child_env(owned, redis_url)
    port = _allocate_port()
    try:
        web = _start_xagent(owned, environment, port)
    except SystemExit:
        terminate_tree(redis_identity)
        raise
    _write_state(
        owned,
        {
            "owner": OWNERSHIP_NAME,
            "root": str(owned),
            "base_url": f"http://127.0.0.1:{port}",
            "web": web,
            "redis": redis_identity,
            "redis_url": redis_url,
            "database_url": environment["DATABASE_URL"],
        },
    )
    print(json.dumps(_load_state(owned), indent=2))
    return 0


def cmd_stop(root: Path) -> int:
    owned = _require_owned_root(root)
    state = _load_state(owned) or {}
    terminate_tree(state.get("web"))
    terminate_tree(state.get("redis"))
    state["web"] = None
    state["redis"] = None
    state["owner"] = OWNERSHIP_NAME
    state["root"] = str(owned)
    _write_state(owned, state)
    print(json.dumps({"status": "stopped", "root": str(owned)}))
    return 0


def cmd_status(root: Path) -> int:
    owned = _owned_root(root)
    state = _load_state(owned)
    if state is None:
        print(json.dumps({"status": "stopped", "root": str(owned)}))
        return 1
    if not state.get("web"):
        print(json.dumps({"status": "stopped", "root": str(owned), **state}, indent=2))
        return 1
    try:
        verify_process(state.get("web"))
        process_ok = True
    except ProcessIdentityError:
        process_ok = False
    healthy, ready = _health(str(state.get("base_url") or ""))
    running = process_ok and healthy and ready
    print(
        json.dumps(
            {
                "status": "running" if running else "stale",
                "health": healthy,
                "ready": ready,
                "process_ok": process_ok,
                **state,
            },
            indent=2,
        )
    )
    return 0 if running else 1


def cmd_logs(root: Path) -> int:
    owned = _require_owned_root(root)
    for path in sorted((owned / "logs").glob("*.log")):
        print(f"===== {path.name} =====")
        print(path.read_text()[-12000:])
    return 0


def _seed(root: Path, state: dict) -> None:
    expected_path = _owned_database_path(root)
    expected = f"sqlite:///{expected_path}"
    recorded = state.get("database_url")
    if recorded != expected:
        _fail("Refuse to seed a database_url that is not the owned sqlite path")
    raw = Path(str(recorded).removeprefix("sqlite:///"))
    if raw.is_symlink() or raw.parent.is_symlink():
        _fail("Refuse to seed a symlinked database path")
    actual = raw.resolve()
    try:
        actual.relative_to(root.resolve())
    except ValueError:
        _fail("Refuse to seed a database_url that is not the owned sqlite path")
    if actual != expected_path:
        _fail("Refuse to seed a database_url that is not the owned sqlite path")
    os.environ["DATABASE_URL"] = expected
    os.environ["XAGENT_STORAGE_ROOT"] = str(root / "storage")
    from xagent.web.api.auth import hash_password
    from xagent.web.models.database import get_session_local, init_db
    from xagent.web.models.user import User

    init_db()
    with get_session_local()() as db:
        existing = db.query(User).filter(User.username == PROOF_OWNER_USERNAME).first()
        if existing is None:
            db.add(
                User(
                    username=PROOF_OWNER_USERNAME,
                    password_hash=hash_password(PROOF_OWNER_PASSWORD),
                    is_admin=True,
                )
            )
            db.commit()


def cmd_seed(root: Path) -> int:
    owned = _require_owned_root(root)
    state = _load_state(owned)
    if state is None:
        _fail("Runtime is not started; seed requires an owned stack")
    _seed(owned, state)
    print(json.dumps({"status": "seeded", "username": PROOF_OWNER_USERNAME}))
    return 0


def _rmtree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


def cmd_reset(root: Path) -> int:
    owned = _require_owned_root(root)
    protected = {
        REPO_ROOT,
        Path.home().resolve(),
        Path.home().resolve() / ".xagent",
        Path("/"),
    }
    if owned in protected:
        _fail(f"Refuse to reset {owned}")
    marker = owned / "OWNED_BY_DAGENT_RUNTIME"
    if marker.is_symlink() or owned.is_symlink():
        _fail(f"Refuse to reset symlinked root {owned}")
    if not marker.is_file():
        _fail(f"Refuse to reset unmarked directory {owned}")
    state = _load_state(owned)
    if state is None or state.get("owner") != OWNERSHIP_NAME:
        _fail(f"Refuse to reset unowned directory {owned}")
    _refuse_live_owned(
        _verified_live(state.get("web")),
        _verified_live(state.get("redis")),
        "reset",
    )
    expected_db = _owned_database_path(owned)
    ambient = os.environ.get("DATABASE_URL")
    if ambient:
        raw = ambient.removeprefix("sqlite:///")
        if Path(raw).expanduser().resolve() != expected_db:
            _fail("Refuse to reset an arbitrary DATABASE_URL")
    for name in ("db", "uploads", "storage", "lancedb"):
        target = owned / name
        if target.is_symlink():
            _fail(f"Refuse to reset symlinked path {target}")
        _rmtree(target)
    (owned / "db").mkdir(parents=True, exist_ok=True)
    print(json.dumps({"status": "reset", "root": str(owned)}))
    return 0


def _proof_paths(root: Path, name: str) -> tuple[Path, Path]:
    owned = _claim_fresh_or_owned_root(root)
    proofs = owned / "proofs"
    proofs.mkdir(parents=True, exist_ok=True)
    return proofs / name, proofs / f"{name}.xml"


def cmd_smoke(root: Path) -> int:
    basetemp, junit = _proof_paths(root, "smoke")
    return run_proof_pytest(
        ["tests/e2e/test_api_smoke.py", "--run-special"],
        extra_env={"XAGENT_RUNTIME_PROOF": "smoke"},
        basetemp=basetemp,
        junit_path=junit,
        cwd=REPO_ROOT,
    )


def cmd_ui(root: Path) -> int:
    frontend = REPO_ROOT / "frontend" / "out" / "index.html"
    if not frontend.is_file() and not os.environ.get("XAGENT_FRONTEND_DIST_DIR"):
        _fail(
            "Frontend static export is missing. Build with "
            "`cd frontend && npm ci && NEXT_TELEMETRY_DISABLED=1 npm run build`"
        )
    basetemp, junit = _proof_paths(root, "ui")
    extra = {"XAGENT_RUNTIME_PROOF": "ui"}
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        extra["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    if os.environ.get("XAGENT_FRONTEND_DIST_DIR"):
        extra["XAGENT_FRONTEND_DIST_DIR"] = os.environ["XAGENT_FRONTEND_DIST_DIR"]
    return run_proof_pytest(
        ["tests/e2e/test_ui_smoke.py", "--run-special", "--ui-smoke"],
        extra_env=extra,
        basetemp=basetemp,
        junit_path=junit,
        cwd=REPO_ROOT,
    )


def cmd_real_model(root: Path) -> int:
    key = (os.environ.get("DMXAPI_KEY") or "").strip()
    if not key:
        _fail("DMXAPI_KEY is required for real-model proof")
    basetemp, junit = _proof_paths(root, "real-model")
    extra = {
        "XAGENT_RUNTIME_PROOF": "real-model",
        "DMXAPI_KEY": key,
        "DMXAPI_BASE_URL": os.environ.get("DMXAPI_BASE_URL") or DEFAULT_DMX_BASE_URL,
    }
    return run_proof_pytest(
        ["tests/e2e/test_model_live.py", "--run-special", "--real-model"],
        extra_env=extra,
        basetemp=basetemp,
        junit_path=junit,
        cwd=REPO_ROOT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Isolated runtime root (default: .run/runtime or XAGENT_RUNTIME_ROOT)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "start",
        "stop",
        "status",
        "logs",
        "seed",
        "reset",
        "smoke",
        "ui",
        "real-model",
    ):
        sub.add_parser(name)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root
    if args.command == "start":
        return cmd_start(root)
    if args.command == "stop":
        return cmd_stop(root)
    if args.command == "status":
        return cmd_status(root)
    if args.command == "logs":
        return cmd_logs(root)
    if args.command == "seed":
        return cmd_seed(root)
    if args.command == "reset":
        return cmd_reset(root)
    if args.command == "smoke":
        return cmd_smoke(root)
    if args.command == "ui":
        return cmd_ui(root)
    if args.command == "real-model":
        return cmd_real_model(root)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
