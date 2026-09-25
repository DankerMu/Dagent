from __future__ import annotations

from pathlib import Path

from scripts.engineering.canonical import (
    collect_naming_findings,
    collect_scratch_findings,
    collect_size_findings,
)
from scripts.engineering.config import load_constraints
from tests.engineering.conftest import write_constraints


def test_size_gate_accepts_short_file(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    assert collect_size_findings(clean_repo, constraints) == []


def test_size_gate_rejects_oversize_file(clean_repo: Path) -> None:
    write_constraints(clean_repo, **{"size_limits.max_file_lines.value": 3})
    oversized = clean_repo / "src" / "xagent" / "app.py"
    oversized.write_text("a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)
    findings = collect_size_findings(clean_repo, constraints)
    assert findings
    assert findings[0].check == "size"
    assert findings[0].metric == 5
    assert findings[0].as_dict()["metric"] == 5
    assert "exceed" in findings[0].detail


def test_naming_gate_rejects_v2_suffix(clean_repo: Path) -> None:
    path = clean_repo / "src" / "xagent" / "helper_v2.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)
    findings = collect_naming_findings(clean_repo, constraints)
    assert any(item.path.endswith("helper_v2.py") for item in findings)


def test_naming_matches_suffixes_not_lexical_continuations(clean_repo: Path) -> None:
    allowed = [
        "src/xagent/create_news.py",
        "src/xagent/api_v2beta.py",
        "src/xagent/new_templates/page.py",
    ]
    forbidden = [
        "src/xagent/helper_new.py",
        "src/xagent/worker_v2/task.py",
        "src/xagent/foo_new_new.py",
        "frontend/src/utils_v2.test.ts",
        "frontend/src/parser_new.spec.tsx",
    ]
    for relative in allowed + forbidden:
        path = clean_repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n")
    findings = collect_naming_findings(clean_repo, load_constraints(clean_repo))
    assert {finding.path for finding in findings} == set(forbidden)


def test_scratch_gate_rejects_tmp_directory(clean_repo: Path) -> None:
    path = clean_repo / "tmp" / "note.py"
    path.parent.mkdir()
    path.write_text("print('scratch')\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)
    findings = collect_scratch_findings(clean_repo, constraints)
    assert findings
    assert findings[0].check == "scratchpad"
    assert "tmp" in findings[0].detail


def test_size_gate_skips_generated_out_and_counts_app_build(
    clean_repo: Path,
) -> None:
    write_constraints(clean_repo, **{"size_limits.max_file_lines.value": 1})
    app_build = clean_repo / "frontend" / "src" / "app" / "build" / "page.tsx"
    app_build.parent.mkdir(parents=True, exist_ok=True)
    app_build.write_text("a\nb\nc\n", encoding="utf-8")
    generated = clean_repo / "frontend" / "out" / "index.js"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text("a\nb\nc\nd\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)
    findings = collect_size_findings(clean_repo, constraints)
    paths = {item.path for item in findings}
    assert "frontend/src/app/build/page.tsx" in paths
    assert "frontend/out/index.js" not in paths


def test_size_gate_honors_exemption(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "size_limits.max_file_lines.value": 1,
            "exemptions.entries": [
                {
                    "path": "src/xagent/app.py",
                    "rules": ["size_limits"],
                    "reason": "split later",
                    "exit_condition": "split files",
                }
            ],
        },
    )
    (clean_repo / "src" / "xagent" / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)
    findings = collect_size_findings(clean_repo, constraints)
    assert all(item.path != "src/xagent/app.py" for item in findings)


def test_naming_and_scratch_honor_exemptions(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/xagent/helper_v2.py",
                    "rules": ["naming"],
                    "reason": "legacy",
                    "exit_condition": "rename",
                },
                {
                    "path": "tmp",
                    "rules": ["scratchpad"],
                    "reason": "scratch",
                    "exit_condition": "delete",
                },
            ]
        },
    )
    (clean_repo / "src" / "xagent" / "helper_v2.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    note = clean_repo / "tmp" / "note.py"
    note.parent.mkdir()
    note.write_text("print('scratch')\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)
    assert collect_naming_findings(clean_repo, constraints) == []
    assert collect_scratch_findings(clean_repo, constraints) == []


def test_size_gate_skips_undecodable_source(clean_repo: Path) -> None:
    write_constraints(clean_repo, **{"size_limits.max_file_lines.value": 1})
    binaryish = clean_repo / "src" / "xagent" / "weird.py"
    binaryish.write_bytes(b"\xff\xfe\x00\x01" * 20)
    constraints = load_constraints(clean_repo)
    findings = collect_size_findings(clean_repo, constraints)
    assert all(item.path != "src/xagent/weird.py" for item in findings)
