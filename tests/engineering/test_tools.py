from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts.engineering.tools import (
    ToolFailure,
    ToolResult,
    parse_json_output,
    require_executable,
    require_frontend_bin,
    run_command,
)


def test_run_command_returns_stdout_and_exit() -> None:
    result = run_command(
        [sys.executable, "-c", "import sys; print('hello'); sys.exit(7)"],
        cwd=Path.cwd(),
    )
    assert result.returncode == 7
    assert result.stdout.strip() == "hello"


def test_run_command_merges_env_and_stdin() -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import os, sys; print(os.environ['ENG_TEST_TOKEN']); print(sys.stdin.read())",
        ],
        cwd=Path.cwd(),
        env={"ENG_TEST_TOKEN": "from-env"},
        stdin="from-stdin",
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines == ["from-env", "from-stdin"]


def test_run_command_timeout_is_tool_failure() -> None:
    with pytest.raises(ToolFailure, match="timed out after 1s"):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=Path.cwd(),
            timeout=1,
        )


def test_run_command_missing_binary_is_tool_failure() -> None:
    with pytest.raises(ToolFailure, match="is not installed"):
        run_command(["definitely-missing-dagent-detector"], cwd=Path.cwd())


def test_require_frontend_bin_missing_is_tool_failure(tmp_path: Path) -> None:
    with pytest.raises(ToolFailure, match="not installed at"):
        require_frontend_bin(tmp_path, "jscpd")


def test_require_frontend_bin_returns_existing_path(tmp_path: Path) -> None:
    path = tmp_path / "frontend" / "node_modules" / ".bin" / "jscpd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    assert require_frontend_bin(tmp_path, "jscpd") == str(path)


def test_parse_json_output_allows_empty_when_requested() -> None:
    result = ToolResult("knip", ("knip",), 0, "  \n", "", None)
    assert parse_json_output(result, allow_empty=True) is None


def test_parse_json_output_empty_stdout_includes_stderr() -> None:
    result = ToolResult("semgrep", ("semgrep",), 2, "", "parser crashed", None)
    with pytest.raises(ToolFailure, match="parser crashed"):
        parse_json_output(result)


def test_require_executable_returns_existing_binary() -> None:
    path = require_executable("python3")
    assert Path(path).is_file()
