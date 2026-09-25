"""Line movement is not new debt; new matches cannot inherit old identities."""

from pathlib import Path

import pytest

from scripts.engineering.adapters import (
    parse_knip_payload,
    parse_semgrep_payload,
    parse_vulture_output,
)
from scripts.engineering.config import load_constraints
from scripts.engineering.findings import compare_findings, serialize_baseline
from scripts.engineering.tools import ToolFailure


def _semgrep(root, lines):
    source = (root / "src/xagent/app.py").read_text().splitlines()
    results = [
        {
            "path": "src/xagent/app.py",
            "check_id": "fixture.forbidden-call",
            "start": {"line": line, "col": 1},
            "end": {"line": line, "col": len(source[line - 1]) + 1},
            "extra": {"severity": "ERROR", "lines": source[line - 1]},
        }
        for line in lines
    ]
    return parse_semgrep_payload(
        root, load_constraints(root), {"errors": [], "results": results}
    )


def test_semgrep_identity_survives_line_shift_but_rejects_same_rule_swap(clean_repo):
    source = clean_repo / "src/xagent/app.py"
    original = "bad_call('alpha')\nbad_call('beta')\n"
    source.write_text(original)
    findings = _semgrep(clean_repo, [1, 2])
    frozen = serialize_baseline(
        reference="fixture", tool_identities={}, findings=findings
    )
    source.write_text("# harmless header\n" + original)
    assert compare_findings(_semgrep(clean_repo, [2, 3]), frozen) == []
    source.write_text("# harmless header\nbad_call('alpha')\nbad_call('gamma')\n")
    assert compare_findings(_semgrep(clean_repo, [2, 3]), frozen)


def test_identical_semgrep_matches_retain_multiplicity(clean_repo):
    source = clean_repo / "src/xagent/app.py"
    source.write_text("bad_call('same')\n" * 2)
    findings = _semgrep(clean_repo, [1, 2])
    assert len({finding.fingerprint for finding in findings}) == 2
    frozen = serialize_baseline(
        reference="fixture", tool_identities={}, findings=findings
    )
    source.write_text("# header\n" + "bad_call('same')\n" * 2)
    assert compare_findings(_semgrep(clean_repo, [2, 3]), frozen) == []
    source.write_text("bad_call('same')\n" * 3)
    assert compare_findings(_semgrep(clean_repo, [1, 2, 3]), frozen)


def test_vulture_identity_survives_line_shift_without_same_symbol_swap(clean_repo):
    source = clean_repo / "src/xagent/app.py"
    source.write_text("unused = 1\nunused = 2\n")
    constraints = load_constraints(clean_repo)

    def collect(lines):
        report = "\n".join(
            f"src/xagent/app.py:{line}: unused variable 'unused' (100% confidence)"
            for line in lines
        )
        return parse_vulture_output(clean_repo, constraints, report)

    findings = collect([1, 2])
    assert len({finding.fingerprint for finding in findings}) == 2
    frozen = serialize_baseline(
        reference="fixture", tool_identities={}, findings=findings
    )
    source.write_text("# header\nunused = 1\nunused = 2\n")
    assert compare_findings(collect([2, 3]), frozen) == []
    source.write_text("# header\nunused = 1\nunused = 3\n")
    assert compare_findings(collect([2, 3]), frozen)


def test_knip_named_export_identity_does_not_depend_on_line(clean_repo):
    constraints = load_constraints(clean_repo)

    def collect(line):
        return parse_knip_payload(
            clean_repo,
            constraints,
            {
                "issues": [
                    {
                        "file": "src/lib/ok.ts",
                        "exports": [{"name": "unused", "line": line}],
                    }
                ]
            },
        )

    original = collect(2)
    frozen = serialize_baseline(
        reference="fixture", tool_identities={}, findings=original
    )
    assert compare_findings(collect(3), frozen) == []


def test_vulture_missing_source_fails_closed(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    (clean_repo / "src/xagent/app.py").unlink()
    with pytest.raises(ToolFailure, match="cannot read source"):
        parse_vulture_output(
            clean_repo,
            constraints,
            "src/xagent/app.py:1: unused function 'dead'\n",
        )


def test_vulture_invalid_line_fails_closed(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="source coordinates are invalid"):
        parse_vulture_output(
            clean_repo,
            constraints,
            "src/xagent/app.py:9: unused function 'dead'\n",
        )


def test_semgrep_missing_source_fails_closed(clean_repo: Path) -> None:
    (clean_repo / "src/xagent/app.py").unlink()
    with pytest.raises(ToolFailure, match="cannot read source"):
        parse_semgrep_payload(
            clean_repo,
            load_constraints(clean_repo),
            {
                "errors": [],
                "results": [
                    {
                        "path": "src/xagent/app.py",
                        "check_id": "fixture.forbidden-call",
                        "start": {"line": 1, "col": 1},
                        "end": {"line": 1, "col": 2},
                        "extra": {"severity": "ERROR", "lines": "requires login"},
                    }
                ],
            },
        )


def test_semgrep_invalid_bounds_fail_closed(clean_repo: Path) -> None:
    source = clean_repo / "src/xagent/app.py"
    source.write_text("eval('x')\n", encoding="utf-8")
    constraints = load_constraints(clean_repo)

    def payload(start, end):
        return {
            "errors": [],
            "results": [
                {
                    "path": "src/xagent/app.py",
                    "check_id": "fixture.forbidden-call",
                    "start": start,
                    "end": end,
                    "extra": {"severity": "ERROR", "lines": "requires login"},
                }
            ],
        }

    with pytest.raises(ToolFailure, match="source coordinates are invalid"):
        parse_semgrep_payload(
            clean_repo,
            constraints,
            payload({"line": 2, "col": 1}, {"line": 2, "col": 2}),
        )
    with pytest.raises(ToolFailure, match="source coordinates are invalid"):
        parse_semgrep_payload(
            clean_repo,
            constraints,
            payload({"line": 1, "col": 1}, {"line": 1, "col": 99}),
        )
    with pytest.raises(ToolFailure, match="source coordinates are invalid"):
        parse_semgrep_payload(
            clean_repo,
            constraints,
            payload({"line": 1, "col": 4}, {"line": 1, "col": 1}),
        )


def test_semgrep_ignores_extra_lines_payload(clean_repo: Path) -> None:
    source = clean_repo / "src/xagent/app.py"
    source.write_text("bad_call('alpha')\n", encoding="utf-8")
    findings = _semgrep(clean_repo, [1])
    frozen = serialize_baseline(
        reference="fixture", tool_identities={}, findings=findings
    )
    source.write_text("bad_call('beta')\n", encoding="utf-8")
    swapped = parse_semgrep_payload(
        clean_repo,
        load_constraints(clean_repo),
        {
            "errors": [],
            "results": [
                {
                    "path": "src/xagent/app.py",
                    "check_id": "fixture.forbidden-call",
                    "start": {"line": 1, "col": 1},
                    "end": {"line": 1, "col": 17},
                    "extra": {"severity": "ERROR", "lines": "requires login"},
                }
            ],
        },
    )
    assert compare_findings(swapped, frozen)
