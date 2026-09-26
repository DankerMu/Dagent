from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.engineering.adapters import (
    collect_detect_secrets,
    collect_jscpd,
    collect_knip,
    collect_lizard,
    collect_semgrep,
    collect_vulture,
    tool_identity,
)
from scripts.engineering.config import load_constraints
from scripts.engineering.tools import ToolFailure, ToolResult
from tests.engineering.test_adapters import lizard_csv, lizard_row


def _capture_run(
    monkeypatch, stdout: str = "{}", returncode: int = 0, stderr: str = ""
):
    captured: dict[str, object] = {}

    def fake_run(argv, *, cwd, timeout=120, env=None, stdin=None):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        return ToolResult(argv[0], tuple(argv), returncode, stdout, stderr)

    monkeypatch.setattr("scripts.engineering.adapters.run_command", fake_run)
    return captured


def _frontend_bin(root: Path, name: str) -> Path:
    local = root / "frontend" / "node_modules" / ".bin" / name
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("#!/bin/sh\n", encoding="utf-8")
    return local


def test_detect_secrets_scans_explicit_git_files_not_all_files(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, *, cwd, timeout=120, env=None, stdin=None):
        captured["argv"] = list(argv)
        return ToolResult(argv[0], tuple(argv), 0, '{"results":{}}', "")

    monkeypatch.setattr(
        "scripts.engineering.adapters.list_scannable_files",
        lambda root: [
            "src/xagent/app.py",
            "frontend/src/lib/ok.ts",
            ".engineering/baseline.json",
        ],
    )
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable",
        lambda name: "/bin/detect-secrets",
    )
    monkeypatch.setattr("scripts.engineering.adapters.run_command", fake_run)
    constraints = load_constraints(clean_repo)
    assert collect_detect_secrets(clean_repo, constraints) == []
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--all-files" not in argv
    assert argv[:3] == ["/bin/detect-secrets", "scan", "--force-use-all-plugins"]
    assert "src/xagent/app.py" in argv
    assert "frontend/src/lib/ok.ts" in argv
    assert ".engineering/baseline.json" not in argv


def test_lizard_collect_excludes_generated_paths_from_scan(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = lizard_csv(lizard_row(ccn=22, name="heavy"))
    captured = _capture_run(monkeypatch, stdout=csv, returncode=1)
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/lizard"
    )
    constraints = load_constraints(clean_repo)
    findings = collect_lizard(clean_repo, constraints)
    argv = captured["argv"]
    assert isinstance(argv, list)
    excludes = [
        argv[index + 1] for index, token in enumerate(argv) if token == "--exclude"
    ]
    assert "*/frontend_dist/*" in excludes
    assert "*/uploads/*" in excludes
    assert "-L" in argv
    assert findings[0].path == "src/xagent/app.py"
    assert findings[0].check == "complexity"


def test_lizard_nonzero_exit_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_run(monkeypatch, stdout="", returncode=2, stderr="lizard crashed")
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/lizard"
    )
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="lizard failed"):
        collect_lizard(clean_repo, constraints)


def test_vulture_omits_missing_engineering_dir(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.rmtree(clean_repo / "scripts" / "engineering")
    captured = _capture_run(
        monkeypatch,
        stdout="src/xagent/app.py:1: unused function 'greet'\n",
        returncode=3,
    )
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/vulture"
    )
    constraints = load_constraints(clean_repo)
    findings = collect_vulture(clean_repo, constraints)
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "scripts/engineering" not in argv
    assert findings[0].check == "dead_code"


def test_semgrep_omits_missing_engineering_dir(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.rmtree(clean_repo / "scripts" / "engineering")
    captured = _capture_run(
        monkeypatch,
        stdout='{"errors":[],"results":[]}',
        returncode=0,
    )
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/semgrep"
    )
    constraints = load_constraints(clean_repo)
    assert collect_semgrep(clean_repo, constraints) == []
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "scripts/engineering" not in argv
    assert "--no-rewrite-rule-ids" in argv


def test_semgrep_missing_config_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_repo / ".semgrep.yml").unlink()
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/semgrep"
    )
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match=".semgrep.yml is missing"):
        collect_semgrep(clean_repo, constraints)


def test_jscpd_omits_missing_engineering_dir_and_ignores_generated(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.rmtree(clean_repo / "scripts" / "engineering")
    _frontend_bin(clean_repo, "jscpd")
    captured = _capture_run(
        monkeypatch,
        stdout='{"statistics":{"total":{"percentage":0}},"duplicates":[]}',
        returncode=0,
    )
    constraints = load_constraints(clean_repo)
    assert collect_jscpd(clean_repo, constraints) == []
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "scripts/engineering" not in argv
    ignore = argv[argv.index("--ignore") + 1]
    assert "frontend_dist" in ignore
    assert "uploads" in ignore


def test_jscpd_malformed_report_file_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frontend_bin(clean_repo, "jscpd")
    report = clean_repo / ".run" / "jscpd" / "jscpd-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("{nope", encoding="utf-8")
    _capture_run(monkeypatch, stdout="", returncode=0)
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="malformed JSON"):
        collect_jscpd(clean_repo, constraints)


def test_jscpd_empty_stdout_without_report_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frontend_bin(clean_repo, "jscpd")
    _capture_run(monkeypatch, stdout="", returncode=0)
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="produced no JSON report"):
        collect_jscpd(clean_repo, constraints)


def test_jscpd_stdout_fallback_parses_when_report_missing(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frontend_bin(clean_repo, "jscpd")
    _capture_run(
        monkeypatch,
        stdout='{"statistics":{"total":{"percentage":0}},"duplicates":[]}',
        returncode=0,
    )
    constraints = load_constraints(clean_repo)
    assert collect_jscpd(clean_repo, constraints) == []


def test_knip_nonzero_exit_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frontend_bin(clean_repo, "knip")
    _capture_run(monkeypatch, stdout="", returncode=2, stderr="knip crashed")
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="knip failed"):
        collect_knip(clean_repo, constraints)


def test_detect_secrets_empty_scan_list_returns_no_findings(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.adapters.list_scannable_files", lambda root: []
    )
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable",
        lambda name: "/bin/detect-secrets",
    )
    constraints = load_constraints(clean_repo)
    assert collect_detect_secrets(clean_repo, constraints) == []


def test_tool_identity_uses_version_stdout(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_run(monkeypatch, stdout="lizard 1.24.0\n")
    assert tool_identity(clean_repo, "/bin/lizard") == "lizard 1.24.0"


def test_tool_identity_falls_back_to_executable_name(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_run(monkeypatch, stdout="", stderr="")
    assert tool_identity(clean_repo, "/bin/lizard") == "/bin/lizard"


def test_vulture_nonzero_exit_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_run(monkeypatch, stdout="", returncode=2, stderr="vulture crashed")
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/vulture"
    )
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="vulture failed"):
        collect_vulture(clean_repo, constraints)


def test_semgrep_nonzero_exit_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_run(
        monkeypatch,
        stdout='{"results":[{"extra":{"message":"password = \\"hunter2\\""}}]}',
        returncode=2,
        stderr="semgrep crashed",
    )
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable", lambda name: "/bin/semgrep"
    )
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="semgrep failed") as excinfo:
        collect_semgrep(clean_repo, constraints)
    assert "hunter2" not in str(excinfo.value)
    assert "semgrep crashed" in str(excinfo.value)


def test_jscpd_nonzero_exit_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frontend_bin(clean_repo, "jscpd")
    _capture_run(monkeypatch, stdout="", returncode=2, stderr="jscpd crashed")
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="jscpd failed"):
        collect_jscpd(clean_repo, constraints)


def test_jscpd_malformed_stdout_fallback_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frontend_bin(clean_repo, "jscpd")
    _capture_run(monkeypatch, stdout="{nope", returncode=0)
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="malformed JSON"):
        collect_jscpd(clean_repo, constraints)


def test_detect_secrets_nonzero_exit_is_tool_failure(
    clean_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.adapters.list_scannable_files",
        lambda root: ["src/xagent/app.py"],
    )
    monkeypatch.setattr(
        "scripts.engineering.adapters.require_executable",
        lambda name: "/bin/detect-secrets",
    )
    _capture_run(monkeypatch, stdout="", returncode=2, stderr="scan crashed")
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="detect-secrets failed"):
        collect_detect_secrets(clean_repo, constraints)
