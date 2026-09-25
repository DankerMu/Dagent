"""Run external detectors and distinguish findings from tool failure."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ToolFailure(RuntimeError):
    """The detector did not produce a usable report."""


@dataclass(frozen=True)
class ToolResult:
    name: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    parsed: object | None = None


def which(name: str) -> str | None:
    return shutil.which(name)


def require_executable(name: str) -> str:
    path = which(name)
    if path is None:
        raise ToolFailure(
            f"{name} is not installed; install the pinned engineering dependency and retry"
        )
    return path


def require_frontend_bin(root: Path, name: str) -> str:
    path = root / "frontend" / "node_modules" / ".bin" / name
    if not path.is_file():
        raise ToolFailure(
            f"{name} is not installed at {path.as_posix()}; "
            "install the pinned frontend engineering dependency and retry"
        )
    return str(path)


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> ToolResult:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
            env=merged,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolFailure(f"{argv[0]} is not installed: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure(f"{argv[0]} timed out after {timeout}s") from exc
    return ToolResult(
        name=argv[0],
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def parse_json_output(result: ToolResult, *, allow_empty: bool = False) -> object:
    text = result.stdout.strip()
    if not text:
        if allow_empty:
            return None
        raise ToolFailure(
            f"{result.name} produced no JSON (exit {result.returncode}): {result.stderr.strip() or 'empty stdout'}"
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolFailure(
            f"{result.name} produced malformed JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
        ) from exc
