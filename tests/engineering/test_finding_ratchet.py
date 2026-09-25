from __future__ import annotations

from scripts.engineering.findings import (
    compare_findings,
    make_finding,
    serialize_baseline,
)


def _baseline(*findings):
    return serialize_baseline(
        reference="abc123",
        tool_identities={"lizard": "lizard 1"},
        findings=list(findings),
    )


def test_size_decrease_passes_and_growth_fails() -> None:
    frozen = make_finding(
        check="size",
        path="frontend/src/ci/frontend-test-manifest.test.ts",
        line=801,
        detector="file-size",
        severity="error",
        identity="physical-lines",
        metric=1534,
        detail="physical source lines 1534 exceed 800",
    )
    reduced = make_finding(
        check="size",
        path="frontend/src/ci/frontend-test-manifest.test.ts",
        line=801,
        detector="file-size",
        severity="error",
        identity="physical-lines",
        metric=1509,
        detail="physical source lines 1509 exceed 800",
    )
    grown = make_finding(
        check="size",
        path="frontend/src/ci/frontend-test-manifest.test.ts",
        line=801,
        detector="file-size",
        severity="error",
        identity="physical-lines",
        metric=1600,
        detail="physical source lines 1600 exceed 800",
    )
    new_file = make_finding(
        check="size",
        path="src/xagent/new.py",
        line=801,
        detector="file-size",
        severity="error",
        identity="physical-lines",
        metric=900,
        detail="physical source lines 900 exceed 800",
    )
    assert frozen.fingerprint == reduced.fingerprint
    assert compare_findings([reduced], _baseline(frozen)) == []
    errors = compare_findings([grown], _baseline(frozen))
    assert any("worsened size" in item for item in errors)
    new_errors = compare_findings([new_file], _baseline(frozen))
    assert any("new size finding" in item for item in new_errors)


def test_complexity_and_function_size_permit_reductions() -> None:
    frozen_ccn = make_finding(
        check="complexity",
        path="src/xagent/app.py",
        line=10,
        detector="lizard",
        severity="error",
        identity="heavy",
        metric=22,
        include_line=False,
        detail="cyclomatic complexity 22 exceeds 15 in heavy",
    )
    reduced_ccn = make_finding(
        check="complexity",
        path="src/xagent/app.py",
        line=40,
        detector="lizard",
        severity="error",
        identity="heavy",
        metric=18,
        include_line=False,
        detail="cyclomatic complexity 18 exceeds 15 in heavy",
    )
    grown_ccn = make_finding(
        check="complexity",
        path="src/xagent/app.py",
        line=40,
        detector="lizard",
        severity="error",
        identity="heavy",
        metric=30,
        include_line=False,
        detail="cyclomatic complexity 30 exceeds 15 in heavy",
    )
    frozen_len = make_finding(
        check="function_size",
        path="src/xagent/app.py",
        line=8,
        detector="lizard",
        severity="error",
        identity="long_fn",
        metric=140,
        include_line=False,
        detail="function length 140 exceeds 100 in long_fn",
    )
    reduced_len = make_finding(
        check="function_size",
        path="src/xagent/app.py",
        line=8,
        detector="lizard",
        severity="error",
        identity="long_fn",
        metric=120,
        include_line=False,
        detail="function length 120 exceeds 100 in long_fn",
    )
    assert frozen_ccn.fingerprint == reduced_ccn.fingerprint
    assert compare_findings([reduced_ccn], _baseline(frozen_ccn)) == []
    assert any(
        "worsened complexity" in item
        for item in compare_findings([grown_ccn], _baseline(frozen_ccn))
    )
    assert compare_findings([reduced_len], _baseline(frozen_len)) == []


def test_duplicate_rate_decrease_passes_and_new_clone_fails() -> None:
    frozen_rate = make_finding(
        check="duplicate_code",
        path=".",
        line=1,
        detector="jscpd",
        severity="error",
        identity="aggregate-rate",
        metric=6.5,
        include_line=False,
        detail="duplication 6.5% exceeds 3%",
    )
    reduced_rate = make_finding(
        check="duplicate_code",
        path=".",
        line=1,
        detector="jscpd",
        severity="error",
        identity="aggregate-rate",
        metric=6.1,
        include_line=False,
        detail="duplication 6.1% exceeds 3%",
    )
    grown_rate = make_finding(
        check="duplicate_code",
        path=".",
        line=1,
        detector="jscpd",
        severity="error",
        identity="aggregate-rate",
        metric=7.0,
        include_line=False,
        detail="duplication 7.0% exceeds 3%",
    )
    clone = make_finding(
        check="duplicate_code",
        path="src/a.py",
        line=4,
        detector="jscpd",
        severity="error",
        identity="src/a.py->src/b.py:abcd",
        include_line=False,
        detail="duplicated block also in src/b.py",
    )
    assert frozen_rate.fingerprint == reduced_rate.fingerprint
    assert compare_findings([reduced_rate], _baseline(frozen_rate)) == []
    assert any(
        "worsened duplicate_code" in item
        for item in compare_findings([grown_rate], _baseline(frozen_rate))
    )
    assert any(
        "new duplicate_code finding" in item
        for item in compare_findings([clone], _baseline(frozen_rate))
    )


def test_other_findings_still_use_fingerprint_and_severity() -> None:
    frozen = make_finding(
        check="sast",
        path="src/xagent/app.py",
        line=4,
        detector="semgrep",
        severity="medium",
        identity="dagent.eval-call",
        detail="old",
    )
    raised = make_finding(
        check="sast",
        path="src/xagent/app.py",
        line=4,
        detector="semgrep",
        severity="error",
        identity="dagent.eval-call",
        detail="worse",
    )
    errors = compare_findings([raised], _baseline(frozen))
    assert any("raised severity" in item for item in errors)
    assert frozen.as_dict().get("metric") is None
