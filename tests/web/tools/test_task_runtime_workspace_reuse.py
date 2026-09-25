"""Provider-prepared workspace must be reused by sandbox resource setup.

``create_default_tools`` builds a real ``TaskWorkspace`` for registered
runtime providers, retains it on the real ``WebToolConfig`` via
``set_task_runtime_workspace``, and later ``ToolFactory`` must consume that
same workspace when preparing sandbox directories.

A plausible ignored/removed setter would force the factory to reconstruct a
workspace from the original config. That reconstruction shares ordinary
task directories but drops provider-owned extra roots, so a resource
created only on the prepared workspace never becomes usable.

Scope: sandbox directory setup after ``create_default_tools``. This does
not claim file-tool or browser-tool instance reuse.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.core.task_runtime import TaskRuntimeContext, TaskRuntimeContribution
from xagent.core.tools.adapters.vibe.selection_spec import ToolSelectionSpec
from xagent.sandbox.base import ExecResult
from xagent.web.services.agent_service_manager import create_default_tools
from xagent.web.services.task_runtime import (
    register_task_extension,
    unregister_task_extension,
)


class _HostExecSandbox:
    """Directory-creation adapter confined to one test-owned root.

    Production ``create_workspace_in_sandbox`` remains intact. Only
    ``mkdir -p`` of directories under ``root`` is permitted; argv is not
    recorded for later assertion.
    """

    name = "host-exec-sandbox"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def exec(self, command: str, *args: str, env=None, max_output_bytes=None):
        if command != "mkdir" or not args or args[0] != "-p":
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr="only mkdir -p of test-owned directories is permitted",
            )
        directories = args[1:]
        if not directories:
            return ExecResult(
                exit_code=1,
                stdout="",
                stderr="mkdir -p requires at least one directory",
            )
        for directory in directories:
            path = Path(directory).expanduser().resolve()
            try:
                path.relative_to(self._root)
            except ValueError:
                return ExecResult(
                    exit_code=1,
                    stdout="",
                    stderr="directory is outside the test-owned root",
                )
        completed = await asyncio.to_thread(
            subprocess.run,
            [command, *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        return ExecResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class _WorkspaceReuseProvider:
    def __init__(self, extra_dir: Path) -> None:
        self._extra_dir = extra_dir

    def on_task_created(self, context: TaskRuntimeContext, configuration: Any) -> None:
        return None

    def build_runtime(self, context: TaskRuntimeContext) -> TaskRuntimeContribution:
        workspace = context.workspace
        if workspace is None:
            return TaskRuntimeContribution()
        workspace.allowed_external_dirs.append(self._extra_dir)
        return TaskRuntimeContribution()

    def public_metadata(self, context: TaskRuntimeContext) -> dict[str, Any] | None:
        return None

    def on_task_deleted(self, context: TaskRuntimeContext) -> None:
        return None


@pytest.fixture
def registered_names() -> Iterator[list[str]]:
    names: list[str] = []
    yield names
    for name in names:
        unregister_task_extension(name)


@pytest.mark.asyncio
async def test_provider_prepared_workspace_is_reused_for_sandbox_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registered_names: list[str],
) -> None:
    """Ignoring set_task_runtime_workspace loses provider-owned sandbox dirs.

    The provider appends a not-yet-created extra root to the prepared
    workspace's public allowed-directory list. Factory sandbox setup must
    mkdir that extra root so a later write can succeed without creating the
    parent. Recreating the workspace from config would omit the extra root
    and the write would fail.
    """
    uploads_dir = tmp_path / "uploads"
    extra_dir = tmp_path / "provider-owned" / "runtime-resource"

    monkeypatch.setenv("XAGENT_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("XAGENT_EXTERNAL_UPLOAD_DIRS", "")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace-reuse.db'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(
        "xagent.web.models.database.get_session_local",
        lambda: factory,
    )

    register_task_extension("workspace_reuse", _WorkspaceReuseProvider(extra_dir))
    registered_names.append("workspace_reuse")

    config = None
    try:
        _tools, config = await create_default_tools(
            None,
            user=SimpleNamespace(id=7, is_admin=False),
            task_id="web_task_11",
            db_task_id=11,
            sandbox=_HostExecSandbox(tmp_path),
            tool_selection_spec=ToolSelectionSpec.from_raw(tool_categories=[]),
            task_runtime_context=TaskRuntimeContext(
                task_id=11,
                user_id=7,
                source="internal",
                session_factory=factory,
            ),
        )
        assert extra_dir.is_dir(), "Provider-owned resources must be materialized"
    finally:
        if config is not None:
            config.close()
        engine.dispose()
