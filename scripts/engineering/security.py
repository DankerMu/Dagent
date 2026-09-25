"""Secret scanning and Semgrep SAST adapters."""

from __future__ import annotations

from pathlib import Path

from .adapters import collect_detect_secrets, collect_semgrep
from .config import ConfigError, load_constraints
from .diffcheck import BOOTSTRAP_APPROVAL_PATH, approved_bootstrap, diff_inventory
from .findings import (
    Finding,
    baseline_content_error,
    baseline_reference_error,
    compare_findings,
    load_baseline,
)
from .gitutil import show_file
from .output import TOOL_FAILURE, report
from .tools import ToolFailure


def collect_security_findings(
    root: Path, *, secrets_only: bool = False
) -> list[Finding]:
    constraints = load_constraints(root)
    findings = collect_detect_secrets(root, constraints)
    if not secrets_only:
        findings.extend(collect_semgrep(root, constraints))
    return findings


def _approval_digests_are_public(root: Path, base: str | None) -> bool:
    path = root / BOOTSTRAP_APPROVAL_PATH
    if path.is_symlink():
        raise ToolFailure(f"{BOOTSTRAP_APPROVAL_PATH} must not be symlinked")
    if not base or not path.is_file():
        return False
    if show_file(root, base, BOOTSTRAP_APPROVAL_PATH) is not None:
        return baseline_content_error(root, path, base) is None
    _, files = diff_inventory(root, base)
    return approved_bootstrap(root, base, files)


def _keep_credential_findings(
    root: Path, base: str | None, findings: list[Finding]
) -> list[Finding]:
    # Only exact verified digest data is exempt, never an arbitrary file path.
    if not _approval_digests_are_public(root, base):
        if any(finding.path == BOOTSTRAP_APPROVAL_PATH for finding in findings):
            print(
                "secrets: bootstrap approval is unverified or modified; use the actual "
                "--base and regenerate before landing, or remove the expired approval.",
                file=__import__("sys").stderr,
            )
        return findings
    return [
        finding
        for finding in findings
        if not (
            finding.path == BOOTSTRAP_APPROVAL_PATH
            and finding.check == "secret"
            and finding.detector == "detect-secrets"
            and finding.detail.startswith("Hex High Entropy String at ")
        )
    ]


def check_security(root: Path, base: str | None, *, secrets_only: bool = False) -> int:
    gate = "secrets" if secrets_only else "security"
    try:
        constraints = load_constraints(root)
        findings = collect_security_findings(root, secrets_only=secrets_only)
        findings = _keep_credential_findings(root, base, findings)
    except ConfigError as exc:
        print(f"{gate}: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    except ToolFailure as exc:
        print(f"{gate}: tool failure: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    artifact = root / constraints.baseline_artifact
    errors: list[str] = []
    if not artifact.is_file():
        errors.append(
            f"{constraints.baseline_artifact} is absent; unknown remains unknown. "
            "Capture against the pristine reference SHA before treating findings as frozen."
        )
        for finding in findings:
            errors.append(
                f"{finding.path}:{finding.line}  {finding.detail} "
                f"(fingerprint {finding.fingerprint})"
            )
        return report(gate, errors, "")
    try:
        baseline = load_baseline(artifact)
    except (OSError, ValueError) as exc:
        print(f"{gate}: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    reference_error = baseline_reference_error(baseline, constraints.baseline_rev)
    if reference_error:
        return report(gate, [reference_error], "")
    try:
        content_error = baseline_content_error(root, artifact, base)
    except ToolFailure as exc:
        print(f"{gate}: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    if content_error:
        return report(gate, [content_error], "")
    relevant = [
        finding
        for finding in findings
        if finding.check in ({"secret"} if secrets_only else {"secret", "sast"})
    ]
    errors.extend(compare_findings(relevant, baseline))
    if errors:
        return report(gate, errors, "")
    scanner = "detect-secrets"
    extra = "" if secrets_only else " and semgrep"
    return report(
        gate,
        [],
        f"{len(relevant)} {scanner}{extra} findings within frozen baseline, all conform",
    )
