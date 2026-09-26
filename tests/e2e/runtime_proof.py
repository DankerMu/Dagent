"""Isolated real-host fixtures for API, UI, and real-model proofs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.e2e.app_harness import build_access_token
from tests.e2e.shared_execution_harness import SharedExecutionApp

PROOF_OWNER_USERNAME = "e2e-owner"
PROOF_OWNER_PASSWORD = (
    "e2e-owner-password"  # pragma: allowlist secret - disposable fixture
)
DEFAULT_DMX_BASE_URL = "https://www.dmxapi.cn/v1"
REAL_MODEL_NAME = "deepseek-v4.1-flash"
REAL_MODEL_ID = "dmx-deepseek-v4-1-flash"
COLLECTION_NAME = "runtime-proof-kb"
_CHILD_PATH_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
)
_CHILD_RUNTIME_KEYS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "UV_PROJECT",
    "UV_PYTHON",
    "XAGENT_TEST_REDIS_URL",
)


def frontend_dist_dir() -> Path:
    configured = os.environ.get("XAGENT_FRONTEND_DIST_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "out"


def require_frontend_dist() -> Path:
    dist = frontend_dist_dir()
    if not (dist / "index.html").is_file():
        pytest.fail(
            "Frontend static export is missing. Build it with "
            "`cd frontend && npm ci && NEXT_TELEMETRY_DISABLED=1 npm run build` "
            f"and set XAGENT_FRONTEND_DIST_DIR to {dist}."
        )
    return dist


def require_dmx_api_key() -> str:
    key = (os.environ.get("DMXAPI_KEY") or "").strip()
    if not key or key.lower() in {"your-api-key", "test-key"}:
        pytest.fail(
            "DMXAPI_KEY is required for the real-model proof and must not be empty."
        )
    return key


def dmx_base_url() -> str:
    return (os.environ.get("DMXAPI_BASE_URL") or DEFAULT_DMX_BASE_URL).rstrip("/")


def child_host_environment(
    parent: dict[str, str],
    *,
    isolated: dict[str, str],
) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in _CHILD_PATH_KEYS + _CHILD_RUNTIME_KEYS:
        value = parent.get(key)
        if value:
            environment[key] = value
    environment.update(isolated)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["LANGFUSE_TRACING_ENABLED"] = "false"
    environment["XAGENT_RUNTIME_PROOF_CHILD"] = "1"
    return environment


def seed_proof_owner(session_factory, *, username: str, password: str) -> int:
    from xagent.web.api.auth import hash_password
    from xagent.web.models.user import User

    with session_factory()() as db:
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
        )
        db.add(user)
        db.commit()
        return int(user.id)


def start_proof_hosts(
    tmp_path: Path,
    monkeypatch,
    *,
    install_model_boundary: bool,
    extra_env: dict[str, str] | None = None,
    require_frontend: bool = False,
):
    from xagent.web.models.database import get_engine, get_session_local, init_db

    root = tmp_path / "hosts"
    root.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()
    (tmp_path / "home").mkdir()
    isolated = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'shared.db'}",
        "XAGENT_UPLOADS_DIR": str(tmp_path / "uploads"),
        "XAGENT_FILE_MATERIALIZE_DIR": str(tmp_path / "materialized"),
        "XAGENT_STORAGE_ROOT": str(storage),
        "HOME": str(tmp_path / "home"),
        "LANCEDB_DIR": str(tmp_path / "lancedb"),
        "LANCEDB_PATH": str(tmp_path / "lancedb-path"),
        "LANCEDB_AUTO_MIGRATE": "false",
        "LANGFUSE_TRACING_ENABLED": "false",
        "ENVIRONMENT": "development",
        "XAGENT_CELERY_ENABLED": "false",
        "XAGENT_CHANNEL_INGRESS_ENABLED": "false",
        "XAGENT_SHARED_TASK_EXECUTION_ENABLED": "true",
        "XAGENT_TASK_LEASE_TTL_SECONDS": "10",
        "XAGENT_TASK_LEASE_HEARTBEAT_SECONDS": "2",
        "XAGENT_TASK_LEASE_RECOVERY_INTERVAL_SECONDS": "1",
        "XAGENT_TRIGGER_DISPATCHER_INTERVAL_SECONDS": "1",
        "XAGENT_TRIGGER_DISPATCHER_STARTUP_JITTER_SECONDS": "0",
        "XAGENT_FILE_STORAGE_STARTUP_SYNC_ENABLED": "false",
        "XAGENT_REDIS_URL": os.environ["XAGENT_REDIS_URL"],
        "XAGENT_TASK_EVENT_CHANNEL_PREFIX": os.environ[
            "XAGENT_TASK_EVENT_CHANNEL_PREFIX"
        ],
        "ENCRYPTION_KEY": os.environ["ENCRYPTION_KEY"],
    }
    if require_frontend:
        isolated["XAGENT_FRONTEND_DIST_DIR"] = str(require_frontend_dist())
    if extra_env:
        isolated.update(extra_env)
    for key, value in isolated.items():
        monkeypatch.setenv(key, value)
    init_db()
    user_id = seed_proof_owner(
        get_session_local,
        username=PROOF_OWNER_USERNAME,
        password=PROOF_OWNER_PASSWORD,
    )
    app = SharedExecutionApp(
        root,
        child_host_environment(os.environ, isolated=isolated),
        build_access_token(username=PROOF_OWNER_USERNAME, user_id=user_id),
        user_id,
    )
    try:
        app.start("web", install_model_boundary=install_model_boundary)
        app.start("worker", install_model_boundary=install_model_boundary)
        yield app
    finally:
        try:
            app.close()
        finally:
            get_engine().dispose()


@pytest.fixture
def proof_app(tmp_path, monkeypatch):
    yield from start_proof_hosts(
        tmp_path,
        monkeypatch,
        install_model_boundary=True,
        require_frontend=False,
    )


@pytest.fixture
def ui_proof_app(tmp_path, monkeypatch):
    yield from start_proof_hosts(
        tmp_path,
        monkeypatch,
        install_model_boundary=True,
        require_frontend=True,
    )


@pytest.fixture
def real_model_app(tmp_path, monkeypatch):
    yield from start_proof_hosts(
        tmp_path,
        monkeypatch,
        install_model_boundary=False,
        require_frontend=False,
    )
