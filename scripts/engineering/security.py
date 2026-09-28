"""Secret scanning and Semgrep SAST adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .adapters import collect_detect_secrets, collect_semgrep
from .config import ConfigError, load_constraints
from .diffcheck import (
    BOOTSTRAP_APPROVAL_PATH,
    OFFLINE_CLEANUP_APPROVAL_PATH,
    RAGFLOW_APPROVAL_PATH,
    RAGFLOW_SCOPE,
    approved_bootstrap,
    approved_offline_cleanup,
    approved_snapshot,
    diff_inventory,
    offline_approval_metadata_valid,
    ragflow_approval_metadata_valid,
)
from .findings import (
    Finding,
    baseline_content_error,
    baseline_reference_error,
    compare_findings,
    fingerprint_for,
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


def _approval_digests_are_public(
    root: Path, base: str | None, approval_path: str
) -> bool:
    path = root / approval_path
    if path.is_symlink():
        raise ToolFailure(f"{approval_path} must not be symlinked")
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ToolFailure(f"{approval_path} must stay inside repository root") from exc
    if not base or not path.is_file():
        return False
    if show_file(root, base, approval_path) is not None:
        if approval_path == OFFLINE_CLEANUP_APPROVAL_PATH:
            if not offline_approval_metadata_valid(path):
                return False
        if approval_path == RAGFLOW_APPROVAL_PATH:
            if not ragflow_approval_metadata_valid(path):
                return False
        return baseline_content_error(root, path, base) is None
    if approval_path == BOOTSTRAP_APPROVAL_PATH:
        _, files = diff_inventory(root, base)
        return approved_bootstrap(root, base, files)
    if approval_path == RAGFLOW_APPROVAL_PATH:
        return approved_snapshot(
            root,
            base,
            [],
            approval_path=approval_path,
            scope=RAGFLOW_SCOPE,
            allow_deletions=True,
        )
    return approved_offline_cleanup(root, base)


def _approval_digest_fingerprints(root: Path, approval_path: str) -> set[str]:
    """Recognize digest values, never arbitrary hex strings in manifest keys."""
    try:
        payload = json.loads((root / approval_path).read_text(encoding="utf-8"))
        digests = {payload["base"]}
        digests.update(
            value for value in payload["files"].values() if isinstance(value, str)
        )
        return {
            fingerprint_for(
                "secret",
                approval_path,
                0,
                "detect-secrets",
                "Hex High Entropy String:"
                + hashlib.sha1(value.encode("utf-8")).hexdigest(),
                include_line=False,
            )
            for value in digests
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return set()


def _keep_credential_findings(
    root: Path, base: str | None, findings: list[Finding]
) -> list[Finding]:
    # Only exact verified digest data is exempt, never an arbitrary file path.
    public_digests: set[str] = set()
    for approval_path, label in (
        (BOOTSTRAP_APPROVAL_PATH, "bootstrap"),
        (OFFLINE_CLEANUP_APPROVAL_PATH, "offline cleanup"),
        (RAGFLOW_APPROVAL_PATH, "RAGFlow"),
    ):
        if _approval_digests_are_public(root, base, approval_path):
            public_digests.update(_approval_digest_fingerprints(root, approval_path))
        elif any(finding.path == approval_path for finding in findings):
            print(
                f"secrets: {label} approval is unverified or modified; use the actual "
                "--base and regenerate before landing, or remove the expired approval.",
                file=__import__("sys").stderr,
            )
    return [
        finding
        for finding in findings
        if not (
            finding.fingerprint in public_digests
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
