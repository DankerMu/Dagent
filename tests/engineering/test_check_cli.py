from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.engineering.check import main

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_help_lists_required_subcommands() -> None:
    with pytest.raises(SystemExit) as exited:
        main(["--help"])
    assert exited.value.code == 0


def test_unknown_flag_is_usage_error() -> None:
    with pytest.raises(SystemExit) as exited:
        main(["--not-a-flag"])
    assert exited.value.code == 2


def test_diff_without_base_is_usage_error(
    clean_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--root", str(clean_repo), "diff"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--base is required" in captured.err


def test_baseline_write_without_reference_is_usage_error(
    clean_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--root", str(clean_repo), "baseline", "--write"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--write requires --reference" in captured.err


def test_coverage_without_reports_fails_closed(
    clean_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--root", str(clean_repo), "coverage"])
    captured = capsys.readouterr()
    assert code == 2
    assert "pytest-json" in captured.err
    assert "vitest-summary" in captured.err


def test_coverage_python_scope_requires_pytest_json(
    clean_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--root", str(clean_repo), "coverage", "--scope", "python"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--pytest-json" in captured.err
    assert "vitest-summary" not in captured.err


def test_coverage_frontend_scope_requires_vitest_summary(
    clean_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--root", str(clean_repo), "coverage", "--scope", "frontend"])
    captured = capsys.readouterr()
    assert code == 2
    assert "--vitest-summary" in captured.err
    assert "pytest-json" not in captured.err


def test_direct_cli_help_runs_without_package() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "engineering" / "check.py"),
            "--help",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "guardrails" in completed.stdout
    assert "ImportError" not in completed.stderr
