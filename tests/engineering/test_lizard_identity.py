from __future__ import annotations

from pathlib import Path

from scripts.engineering.adapters import parse_lizard_csv
from scripts.engineering.config import load_constraints
from scripts.engineering.findings import compare_findings, serialize_baseline
from tests.engineering.test_adapters import lizard_csv, lizard_row


def test_lizard_quoted_comma_signature_keeps_function_identity(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(
            lizard_row(
                ccn=22,
                name="pair",
                signature="pair(left: str, right: str)",
                start=12,
            )
        ),
    )
    assert findings[0].check == "complexity"
    assert findings[0].path == "src/xagent/app.py"
    assert findings[0].line == 12
    assert findings[0].metric == 22
    assert "pair" in findings[0].detail


def test_lizard_absolute_snapshot_and_checkout_roots_share_fingerprint(
    clean_repo: Path, tmp_path: Path
) -> None:
    snapshot = tmp_path / "snapshot"
    checkout = tmp_path / "checkout"
    constraints = load_constraints(clean_repo)
    snapshot_csv = lizard_csv(
        lizard_row(
            ccn=18,
            name="heavy",
            path=str(snapshot / "src" / "xagent" / "app.py"),
            start=40,
        )
    )
    checkout_csv = lizard_csv(
        lizard_row(
            ccn=18,
            name="heavy",
            path=str(checkout / "src" / "xagent" / "app.py"),
            start=40,
        )
    )
    snapshot_findings = parse_lizard_csv(snapshot, constraints, snapshot_csv)
    checkout_findings = parse_lizard_csv(checkout, constraints, checkout_csv)
    assert snapshot_findings[0].path == "src/xagent/app.py"
    assert checkout_findings[0].path == "src/xagent/app.py"
    assert snapshot_findings[0].fingerprint == checkout_findings[0].fingerprint


def test_lizard_flags_excessive_length_despite_low_complexity(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    findings = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(lizard_row(ccn=4, length=140, name="long_fn", start=8)),
    )
    assert [item.check for item in findings] == ["function_size"]
    assert findings[0].line == 8
    assert findings[0].metric == 140
    assert "140" in findings[0].detail


def test_lizard_moved_function_keeps_fingerprint(clean_repo: Path) -> None:
    constraints = load_constraints(clean_repo)
    first = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(lizard_row(ccn=22, name="heavy", start=10)),
    )
    moved = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(lizard_row(ccn=22, name="heavy", start=40)),
    )
    assert first[0].fingerprint == moved[0].fingerprint
    assert moved[0].line == 40


def test_lizard_repeated_method_names_do_not_share_metric_ceiling(
    clean_repo: Path,
) -> None:
    constraints = load_constraints(clean_repo)
    original = lizard_csv(
        lizard_row(name="run", ccn=36, start=10),
        lizard_row(name="run", ccn=17, start=50),
    )
    findings = parse_lizard_csv(clean_repo, constraints, original)
    baseline = serialize_baseline(
        reference="fixture", tool_identities={}, findings=findings
    )
    assert compare_findings(findings, baseline) == []
    grown = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(
            lizard_row(name="run", ccn=36, start=10),
            lizard_row(name="run", ccn=18, start=50),
        ),
    )
    assert any(
        "worsened complexity" in error for error in compare_findings(grown, baseline)
    )


def test_adding_callback_does_not_rename_existing_anonymous_debt(
    clean_repo: Path,
) -> None:
    source = clean_repo / "frontend/src/lib/ok.ts"
    source.write_text('describe("contract", () => {\n')
    constraints = load_constraints(clean_repo)
    original = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(
            lizard_row(
                name="(anonymous)", path="frontend/src/lib/ok.ts", length=120, start=1
            ),
        ),
    )
    baseline = serialize_baseline(
        reference="fixture", tool_identities={}, findings=original
    )
    source.write_text(
        'const mapped = values.map(() => 1)\ndescribe("contract", () => {\n'
    )
    current = parse_lizard_csv(
        clean_repo,
        constraints,
        lizard_csv(
            lizard_row(
                name="(anonymous)", path="frontend/src/lib/ok.ts", length=1, start=1
            ),
            lizard_row(
                name="(anonymous)", path="frontend/src/lib/ok.ts", length=120, start=2
            ),
        ),
    )
    assert compare_findings(current, baseline) == []
