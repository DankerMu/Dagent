"""L3 size, complexity, duplication, dead-code, and naming gate."""

from __future__ import annotations

from pathlib import Path

from .adapters import (
    collect_jscpd,
    collect_knip,
    collect_lizard,
    collect_vulture,
)
from .canonical import (
    collect_naming_findings,
    collect_scratch_findings,
    collect_size_findings,
)
from .config import ConfigError, load_constraints
from .findings import (
    Finding,
    baseline_content_error,
    baseline_reference_error,
    compare_findings,
    load_baseline,
)
from .output import TOOL_FAILURE, report
from .tools import ToolFailure


def collect_guardrail_findings(root: Path) -> list[Finding]:
    constraints = load_constraints(root)
    findings: list[Finding] = []
    findings.extend(collect_size_findings(root, constraints))
    findings.extend(collect_naming_findings(root, constraints))
    findings.extend(collect_scratch_findings(root, constraints))
    findings.extend(collect_lizard(root, constraints))
    findings.extend(collect_vulture(root, constraints))
    findings.extend(collect_knip(root, constraints))
    findings.extend(collect_jscpd(root, constraints))
    return findings


def check_guardrails(root: Path, base: str | None) -> int:
    try:
        constraints = load_constraints(root)
        findings = collect_guardrail_findings(root)
    except ConfigError as exc:
        print(f"guardrails: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    except ToolFailure as exc:
        print(f"guardrails: tool failure: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    artifact = root / constraints.baseline_artifact
    if not artifact.is_file():
        errors = [
            f"{constraints.baseline_artifact} is absent; unknown remains unknown. "
            "Capture with `python scripts/engineering/check.py --root <repo> "
            "baseline --write --reference <SHA>` against pristine HEAD, then retry. "
            "Initialization files cannot be grandfathered."
        ]
        errors.extend(
            f"{finding.path}:{finding.line}  {finding.detail} "
            f"(fingerprint {finding.fingerprint})"
            for finding in findings
        )
        return report("guardrails", errors, "")
    try:
        baseline = load_baseline(artifact)
    except (OSError, ValueError) as exc:
        print(f"guardrails: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    reference_error = baseline_reference_error(baseline, constraints.baseline_rev)
    if reference_error:
        return report("guardrails", [reference_error], "")
    try:
        content_error = baseline_content_error(root, artifact, base)
    except ToolFailure as exc:
        print(f"guardrails: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    if content_error:
        return report("guardrails", [content_error], "")
    try:
        errors = compare_findings(
            findings, baseline, root=root, reference=constraints.baseline_rev
        )
    except ToolFailure as exc:
        print(f"guardrails: tool failure: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    if errors:
        return report("guardrails", errors, "")
    return report(
        "guardrails",
        [],
        f"{len(findings)} findings within frozen baseline, all conform",
    )
