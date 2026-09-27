"""Version metadata served by the running backend, not stale UI assets."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xagent.web.api import system as system_api
from xagent.web.api.system import system_router


def test_version_endpoint_uses_running_build_metadata(monkeypatch) -> None:
    monkeypatch.setenv("XAGENT_VERSION", "v2.4.1.dev9+gabcdef987654")
    monkeypatch.setenv(
        "XAGENT_GIT_COMMIT",
        "1234567890abcdef",  # pragma: allowlist secret - synthetic commit ID
    )
    monkeypatch.setenv("XAGENT_BUILD_TIME", "2026-09-26T09:00:00Z")
    app = FastAPI()
    app.include_router(system_router)

    response = TestClient(app).get("/api/system/version")

    assert response.status_code == 200
    assert response.json() == {
        "version": "v2.4.1.dev9+gabcdef987654",
        "display_version": "v2.4.1-12345",
        "commit": "1234567890ab",  # pragma: allowlist secret - synthetic commit ID
        "build_time": "2026-09-26T09:00:00Z",
    }


def test_version_endpoint_recovers_commit_from_package_local_version(
    monkeypatch,
) -> None:
    monkeypatch.setenv("XAGENT_VERSION", "0.8.2.dev3+gabcdef1234")
    monkeypatch.delenv("XAGENT_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    app = FastAPI()
    app.include_router(system_router)

    response = TestClient(app).get("/api/system/version")

    assert response.status_code == 200
    assert response.json()["display_version"] == "v0.8.2-abcde"


def test_version_endpoint_reads_installed_package_when_build_env_is_absent(
    monkeypatch,
) -> None:
    monkeypatch.delenv("XAGENT_VERSION", raising=False)
    monkeypatch.delenv("XAGENT_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr(
        system_api, "get_package_version", lambda package: "3.2.0.dev4+g12345abc"
    )
    app = FastAPI()
    app.include_router(system_router)

    response = TestClient(app).get("/api/system/version")

    assert response.status_code == 200
    assert response.json()["version"] == "3.2.0.dev4+g12345abc"
    assert response.json()["display_version"] == "v3.2.0-12345"
