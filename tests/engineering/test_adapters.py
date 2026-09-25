from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from scripts.engineering.adapters import (
    parse_detect_secrets_payload,
    parse_jscpd_payload,
    parse_knip_payload,
    parse_lizard_csv,
    parse_semgrep_payload,
    parse_vulture_output,
)
from scripts.engineering.config import load_constraints
from scripts.engineering.tools import ToolFailure
from tests.engineering.conftest import write_constraints


def lizard_row(
    *,
    nloc: int = 10,
    ccn: int | str = 4,
    tokens: int = 20,
    params: int = 1,
    length: int | str = 12,
    name: str = "greet",
    path: str = "src/xagent/app.py",
    signature: str | None = None,
    start: int | str = 1,
    end: int | None = None,
) -> str:
    start_no = int(start) if str(start).replace(".", "", 1).isdigit() else 1
    length_no = int(length) if str(length).replace(".", "", 1).isdigit() else 12
    end_no = end if end is not None else start_no + length_no - 1
    location = f"{name}@{start_no}-{end_no}@{path}"
    buf = io.StringIO()
    csv.writer(buf).writerow(
        [
            nloc,
            ccn,
            tokens,
            params,
            length,
            location,
            path,
            name,
            signature or f"{name}()",
            start,
            end_no,
        ]
    )
    return buf.getvalue().strip()


def lizard_csv(*rows: str) -> str:
    header = (
        "NLOC,CCN,token_count,param_count,length,location,"
        "file,function,signature,start,end"
    )
    return "\n".join((header, *rows)) + "\n"


def knip_issues(*issues: dict) -> dict:
    return {"issues": list(issues)}


def test_lizard_parser_accepts_under_threshold(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    csv_text = lizard_csv(lizard_row())
    assert parse_lizard_csv(clean_repo, constraints, csv_text) == []


def test_lizard_parser_rejects_high_complexity(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_lizard_csv(
        clean_repo, constraints, lizard_csv(lizard_row(ccn=22, name="heavy"))
    )
    assert findings
    assert findings[0].check == "complexity"
    assert findings[0].line == 1
    assert findings[0].path == "src/xagent/app.py"
    assert "22" in findings[0].detail


def test_lizard_parser_rejects_malformed_csv(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="malformed CSV"):
        parse_lizard_csv(clean_repo, constraints, "not,enough\n")


def test_vulture_parser_rejects_malformed_line(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="malformed finding"):
        parse_vulture_output(clean_repo, constraints, "??? boom")


def test_knip_parser_flags_unused_export(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_knip_payload(
        clean_repo,
        constraints,
        knip_issues(
            {
                "file": "src/lib/ok.ts",
                "exports": [{"name": "unused", "line": 2}],
            }
        ),
    )
    assert findings
    assert findings[0].detail == "unused export unused"


def test_knip_parser_rejects_non_object(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="must be an object"):
        parse_knip_payload(clean_repo, constraints, ["nope"])


def test_jscpd_parser_rejects_malformed_percentage(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(
            clean_repo,
            constraints,
            {"statistics": {"total": {"percentage": "high"}}, "duplicates": []},
        )


def test_semgrep_parser_treats_tool_errors_as_failure(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="tool errors") as excinfo:
        parse_semgrep_payload(
            clean_repo,
            constraints,
            {
                "errors": [
                    {
                        "code": "SyntaxError",
                        "path": {"file": "frontend/src/app/page.tsx", "line": 3},
                        "message": "const secret = 'super-secret-source';",  # pragma: allowlist secret - redaction fixture
                    }
                ],
                "results": [],
            },
        )
    message = str(excinfo.value)
    assert "SyntaxError" in message
    assert "frontend/src/app/page.tsx:3" in message
    assert "super-secret-source" not in message


def test_detect_secrets_parser_does_not_keep_raw_secrets(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="secret strings"):
        parse_detect_secrets_payload(
            clean_repo,
            constraints,
            {
                "results": {
                    "src/xagent/app.py": [
                        {
                            "line_number": 1,
                            "secret": "AKIA",  # pragma: allowlist secret -- synthetic payload marker
                        }
                    ]
                }
            },
        )


def test_detect_secrets_parser_fingerprints_hashed_only(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    payload = {
        "results": {
            "src/xagent/app.py": [
                {
                    "line_number": 3,
                    "type": "Secret Keyword",
                    "hashed_secret": "abc",
                }
            ]
        }
    }
    findings = parse_detect_secrets_payload(clean_repo, constraints, payload)
    assert findings[0].check == "secret"
    assert findings[0].line == 3
    assert "AKIA" not in findings[0].detail
    assert findings[0].as_dict().get("secret") is None
    moved = parse_detect_secrets_payload(
        clean_repo,
        constraints,
        {
            "results": {
                "src/xagent/app.py": [
                    {
                        "line_number": 9,
                        "type": "Secret Keyword",
                        "hashed_secret": "abc",
                    }
                ]
            }
        },
    )
    assert moved[0].fingerprint == findings[0].fingerprint
    assert moved[0].line == 9


def test_lizard_parser_skips_exempt_and_non_numeric(
    clean_repo: Path,
) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/xagent/legacy.py",
                    "rules": ["size_limits"],
                    "reason": "split later",
                    "exit_condition": "split files",
                }
            ]
        },
    )
    constraints = load_constraints(clean_repo)
    csv_text = lizard_csv(lizard_row(ccn=99, name="heavy", path="src/xagent/legacy.py"))
    assert parse_lizard_csv(clean_repo, constraints, csv_text) == []
    with pytest.raises(ToolFailure, match="non-numeric CSV"):
        parse_lizard_csv(
            clean_repo,
            constraints,
            lizard_csv(lizard_row(ccn="high", name="heavy")),
        )


def test_vulture_parser_accepts_finding_and_skips_blank(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    (clean_repo / "src/xagent/app.py").write_text(
        "def greet(name: str) -> str:\n    def dead():\n        return name\n    return f'hi {name}'\n",
        encoding="utf-8",
    )
    findings = parse_vulture_output(
        clean_repo,
        constraints,
        "\nsrc/xagent/app.py:2: unused function 'dead'\n",
    )
    assert findings
    assert findings[0].check == "dead_code"
    assert findings[0].detail == "unused function 'dead'"


def test_knip_parser_flags_unused_file_and_rejects_bad_exports(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_knip_payload(
        clean_repo,
        constraints,
        knip_issues(
            {
                "file": "src/lib/orphan.ts",
                "files": [{"name": "src/lib/orphan.ts"}],
            }
        ),
    )
    assert findings[0].path == "frontend/src/lib/orphan.ts"
    assert findings[0].detail == "unused TypeScript file"
    with pytest.raises(ToolFailure, match="exports must be a list"):
        parse_knip_payload(
            clean_repo,
            constraints,
            knip_issues({"file": "src/lib/ok.ts", "exports": {"nope": 1}}),
        )
    with pytest.raises(ToolFailure, match="exports finding must be an object"):
        parse_knip_payload(
            clean_repo,
            constraints,
            knip_issues({"file": "src/lib/ok.ts", "exports": ["nope"]}),
        )
    with pytest.raises(ToolFailure, match="files must be a list"):
        parse_knip_payload(
            clean_repo,
            constraints,
            knip_issues({"file": "src/lib/ok.ts", "files": {"nope": 1}}),
        )
    with pytest.raises(ToolFailure, match="unknown fields"):
        parse_knip_payload(
            clean_repo, constraints, {"files": ["src/lib/orphan.ts"], "exports": []}
        )
    for missing in (None, {}):
        with pytest.raises(ToolFailure):
            parse_knip_payload(clean_repo, constraints, missing)
    assert parse_knip_payload(clean_repo, constraints, {"issues": []}) == []


def test_jscpd_parser_flags_percent_and_clone_pair(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    fragment = "alpha\nbeta\ngamma\ndelta\nepsilon\n"
    findings = parse_jscpd_payload(
        clean_repo,
        constraints,
        {
            "statistics": {"total": {"percentage": 12.5}},
            "duplicates": [
                {
                    "firstFile": {"name": "src/a.py", "start": 4},
                    "secondFile": {"name": "src/b.py"},
                    "fragment": fragment,
                }
            ],
        },
    )
    rate = next(item for item in findings if item.path == ".")
    assert rate.metric == 12.5
    assert any(item.path == "src/a.py" for item in findings)
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(clean_repo, constraints, [])
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(
            clean_repo,
            constraints,
            {"statistics": {"total": {"percentage": 0}}, "duplicates": {"nope": 1}},
        )
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(
            clean_repo,
            constraints,
            {"statistics": {"total": {"percentage": 0}}, "duplicates": ["nope"]},
        )


def test_semgrep_parser_accepts_result_and_rejects_bad_shape(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    source = clean_repo / "src/xagent/app.py"
    source.write_text("eval('forbidden')\n", encoding="utf-8")
    findings = parse_semgrep_payload(
        clean_repo,
        constraints,
        {
            "errors": [],
            "results": [
                {
                    "path": "src/xagent/app.py",
                    "check_id": "rules.no-eval",
                    "start": {"line": 1, "col": 1},
                    "end": {"line": 1, "col": 18},
                    "extra": {"message": "eval is forbidden", "severity": "ERROR"},
                }
            ],
        },
    )
    assert findings[0].check == "sast"
    assert findings[0].detail == "eval is forbidden"
    assert findings[0].severity == "error"
    with pytest.raises(ToolFailure, match="must be an object"):
        parse_semgrep_payload(clean_repo, constraints, [])
    with pytest.raises(ToolFailure, match="results must be a list"):
        parse_semgrep_payload(clean_repo, constraints, {"errors": [], "results": {}})
    with pytest.raises(ToolFailure, match="result must be an object"):
        parse_semgrep_payload(
            clean_repo, constraints, {"errors": [], "results": ["nope"]}
        )


def test_detect_secrets_parser_rejects_malformed_results(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="must be an object"):
        parse_detect_secrets_payload(clean_repo, constraints, [])
    with pytest.raises(ToolFailure, match="results must be an object"):
        parse_detect_secrets_payload(clean_repo, constraints, {"results": []})
    with pytest.raises(ToolFailure, match="must be a list"):
        parse_detect_secrets_payload(
            clean_repo, constraints, {"results": {"src/xagent/app.py": {}}}
        )
    with pytest.raises(ToolFailure, match="finding must be an object"):
        parse_detect_secrets_payload(
            clean_repo, constraints, {"results": {"src/xagent/app.py": ["nope"]}}
        )
    findings = parse_detect_secrets_payload(
        clean_repo,
        constraints,
        {
            "results": {
                "src/xagent/app.py": [
                    {"line_number": 2, "type": "Hex High Entropy String"}
                ]
            }
        },
    )
    assert findings[0].check == "secret"
    assert "Hex High Entropy String" in findings[0].detail


def test_detect_secrets_parser_skips_baseline_artifact(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_detect_secrets_payload(
        clean_repo,
        constraints,
        {
            "results": {
                constraints.baseline_artifact: [
                    {
                        "line_number": 12,
                        "type": "Hex High Entropy String",
                        "hashed_secret": "deadbeef",  # pragma: allowlist secret - checksum fixture
                    }
                ],
                "src/xagent/app.py": [
                    {
                        "line_number": 3,
                        "type": "Secret Keyword",
                        "hashed_secret": "abc",
                    }
                ],
            }
        },
    )
    assert [item.path for item in findings] == ["src/xagent/app.py"]


def test_rel_keeps_absolute_path_outside_root(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure, match="cannot read source"):
        parse_vulture_output(
            clean_repo,
            constraints,
            "/tmp/outside.py:1: unused function 'dead'\n",
        )


def test_rel_normalizes_absolute_path_inside_root(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    source = clean_repo / "src" / "xagent" / "app.py"
    source.write_text(
        "def greet(name: str) -> str:\n    def dead():\n        return name\n    return f'hi {name}'\n",
        encoding="utf-8",
    )
    absolute = str(source)
    findings = parse_vulture_output(
        clean_repo,
        constraints,
        f"{absolute}:2: unused function 'dead'\n",
    )
    assert findings[0].path == "src/xagent/app.py"


def test_jscpd_missing_fragment_is_malformed(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(
            clean_repo,
            constraints,
            {
                "statistics": {"total": {"percentage": 0}},
                "duplicates": [
                    {
                        "firstFile": {"name": "src/xagent/app.py", "start": 4},
                        "secondFile": {"name": "src/xagent/other.py"},
                    }
                ],
            },
        )


def test_jscpd_relative_clone_paths_stay_repo_relative(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    (clean_repo / "frontend" / "src" / "ci").mkdir(parents=True, exist_ok=True)
    (
        clean_repo / "frontend" / "src" / "ci" / "frontend-test-manifest.test.ts"
    ).write_text("export {}\n", encoding="utf-8")
    (clean_repo / "scripts" / "engineering" / "baseline.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    findings = parse_jscpd_payload(
        clean_repo,
        constraints,
        {
            "statistics": {"total": {"percentage": 0}},
            "duplicates": [
                {
                    "firstFile": {
                        "name": str(
                            clean_repo / "scripts" / "engineering" / "baseline.py"
                        ),
                        "start": 1,
                    },
                    "secondFile": {
                        "name": str(
                            clean_repo
                            / "frontend"
                            / "src"
                            / "ci"
                            / "frontend-test-manifest.test.ts"
                        )
                    },
                    "fragment": "one\ntwo\nthree\nfour\nfive\n",
                }
            ],
        },
    )
    assert findings[0].path == "scripts/engineering/baseline.py"
    assert "frontend/src/ci/frontend-test-manifest.test.ts" in findings[0].detail


def test_vulture_parser_skips_exempt_dead_code(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/xagent/app.py",
                    "rules": ["dead_code"],
                    "reason": "legacy",
                    "exit_condition": "delete",
                }
            ]
        },
    )
    constraints = load_constraints(clean_repo)
    assert (
        parse_vulture_output(
            clean_repo,
            constraints,
            "src/xagent/app.py:2: unused function 'dead'\n",
        )
        == []
    )


def test_knip_parser_skips_exempt_and_rejects_non_list_files(
    clean_repo: Path,
) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "frontend/src/lib/orphan.ts",
                    "rules": ["dead_code"],
                    "reason": "legacy",
                    "exit_condition": "delete",
                }
            ]
        },
    )
    constraints = load_constraints(clean_repo)
    assert (
        parse_knip_payload(
            clean_repo,
            constraints,
            knip_issues(
                {
                    "file": "src/lib/orphan.ts",
                    "files": [{"name": "src/lib/orphan.ts"}],
                    "exports": [{"name": "unused", "line": 2}],
                }
            ),
        )
        == []
    )


def test_semgrep_and_secrets_honor_exemptions(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/xagent/app.py",
                    "rules": ["sast", "secrets"],
                    "reason": "legacy",
                    "exit_condition": "delete",
                }
            ]
        },
    )
    constraints = load_constraints(clean_repo)
    assert (
        parse_semgrep_payload(
            clean_repo,
            constraints,
            {
                "errors": [],
                "results": [
                    {
                        "path": "src/xagent/app.py",
                        "check_id": "rules.no-eval",
                        "start": {"line": 9},
                        "extra": {"message": "eval is forbidden"},
                    }
                ],
            },
        )
        == []
    )
    assert (
        parse_detect_secrets_payload(
            clean_repo,
            constraints,
            {
                "results": {
                    "src/xagent/app.py": [
                        {
                            "line_number": 3,
                            "type": "Secret Keyword",
                            "hashed_secret": "abc",
                        }
                    ]
                }
            },
        )
        == []
    )


def test_jscpd_rejects_non_object_clone_files(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(
            clean_repo,
            constraints,
            {
                "statistics": {"total": {"percentage": 0}},
                "duplicates": [{"firstFile": "a.py", "secondFile": "b.py"}],
            },
        )


def test_semgrep_rule_id_strips_config_path_prefix(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    source = clean_repo / "src/xagent/app.py"
    source.write_text("eval('forbidden')\n", encoding="utf-8")
    payload_end = {"line": 1, "col": 18}
    rewritten = parse_semgrep_payload(
        clean_repo,
        constraints,
        {
            "errors": [],
            "results": [
                {
                    "path": "src/xagent/app.py",
                    "check_id": str(clean_repo / ".semgrep.yml") + ".dagent.eval-call",
                    "start": {"line": 1, "col": 1},
                    "end": payload_end,
                    "extra": {
                        "message": "eval() executes arbitrary code from its argument."
                    },
                }
            ],
        },
    )
    canonical = parse_semgrep_payload(
        clean_repo,
        constraints,
        {
            "errors": [],
            "results": [
                {
                    "path": "src/xagent/app.py",
                    "check_id": "dagent.eval-call",
                    "start": {"line": 1, "col": 1},
                    "end": payload_end,
                    "extra": {
                        "message": "eval() executes arbitrary code from its argument."
                    },
                }
            ],
        },
    )
    assert rewritten[0].fingerprint == canonical[0].fingerprint


def test_knip_parser_reads_issue_fields(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_knip_payload(
        clean_repo,
        constraints,
        knip_issues(
            {
                "file": "src/lib/api-wrapper.ts",
                "exports": [{"name": "handleAuthError", "line": 245}],
                "types": [{"name": "AuthError", "line": 12}],
                "dependencies": [{"name": "left-pad", "line": 1}],
            },
            {
                "file": "src/components/agent-input.tsx",
                "files": [{"name": "src/components/agent-input.tsx"}],
            },
        ),
    )
    details = [item.detail for item in findings]
    assert "unused export handleAuthError" in details
    assert "unused types AuthError" in details
    assert "unused dependencies left-pad" in details
    assert any(
        item.path == "frontend/src/components/agent-input.tsx" for item in findings
    )


def test_knip_duplicate_exports_preserve_group_members(clean_repo: Path) -> None:
    findings = parse_knip_payload(
        clean_repo,
        load_constraints(clean_repo),
        knip_issues(
            {
                "file": "src/component.tsx",
                "duplicates": [
                    [
                        {"name": "Component", "line": 12},
                        {"name": "default", "line": 20},
                    ]
                ],
            }
        ),
    )
    assert {(item.path, item.line) for item in findings} == {
        ("frontend/src/component.tsx", 12),
        ("frontend/src/component.tsx", 20),
    }
