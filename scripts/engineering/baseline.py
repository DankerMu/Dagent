"""Capture and compare historical findings. Write only with --write --reference."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .adapters import (
    collect_detect_secrets,
    collect_semgrep,
    tool_identity,
)
from .config import ConfigError, load_constraints
from .findings import (
    Finding,
    baseline_content_error,
    baseline_reference_error,
    compare_findings,
    load_baseline,
    serialize_baseline,
)
from .gitutil import resolve_reference, snapshot_tree
from .guardrails import collect_guardrail_findings
from .output import TOOL_FAILURE, report
from .tools import ToolFailure, require_executable, require_frontend_bin


def collect_all_findings(root: Path) -> list[Finding]:
    constraints = load_constraints(root)
    findings = collect_guardrail_findings(root)
    findings.extend(collect_detect_secrets(root, constraints))
    findings.extend(collect_semgrep(root, constraints))
    return findings


def _identities(root: Path) -> dict[str, str]:
    names = {
        "lizard": "lizard",
        "vulture": "vulture",
        "semgrep": "semgrep",
        "detect-secrets": "detect-secrets",  # pragma: allowlist secret - CLI name
    }
    identities: dict[str, str] = {}
    for label, exe in names.items():
        identities[label] = tool_identity(root, require_executable(exe))
    identities["jscpd"] = tool_identity(root, require_frontend_bin(root, "jscpd"))
    identities["knip"] = tool_identity(root, require_frontend_bin(root, "knip"))
    return identities


def write_baseline(root: Path, reference: str) -> int:
    try:
        constraints = load_constraints(root)
        sha = resolve_reference(root, reference)
    except (ConfigError, ToolFailure) as err:
        print(f"baseline: {err}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    artifact = root / constraints.baseline_artifact
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dagent-baseline-") as tmp:
        snapshot = Path(tmp) / "tree"
        try:
            snapshot_tree(root, sha, snapshot)
            injected_without_history = {
                name
                for name in ("constraints.yaml", ".semgrep.yml", "frontend/knip.json")
                if not (snapshot / name).is_file()
            }
            # Constraints and detector config must match the running gate, not
            # the possibly-absent copies on the pristine revision.
            for name in ("constraints.yaml", ".semgrep.yml", "frontend/knip.json"):
                source = root / name
                target = snapshot / name
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            modules = root / "frontend" / "node_modules"
            if modules.is_dir():
                dest_modules = snapshot / "frontend" / "node_modules"
                dest_modules.parent.mkdir(parents=True, exist_ok=True)
                dest_modules.symlink_to(modules, target_is_directory=True)
            findings = collect_all_findings(snapshot)
            findings = [
                finding
                for finding in findings
                if finding.path not in injected_without_history
            ]
            identities = _identities(root)
        except (ConfigError, ToolFailure, OSError) as err:
            print(f"baseline: {err}", file=__import__("sys").stderr)
            return TOOL_FAILURE
    payload = serialize_baseline(
        reference=sha, tool_identities=identities, findings=findings
    )
    artifact.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report(
        "baseline",
        [],
        (
            f"wrote {artifact} with {len(findings)} findings from pristine {sha}; "
            "initialization files were not grandfathered"
        ),
    )


def compare_baseline(root: Path, base: str | None) -> int:
    try:
        constraints = load_constraints(root)
    except ConfigError as err:
        print(f"baseline: {err}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    artifact = root / constraints.baseline_artifact
    if not artifact.is_file():
        return report(
            "baseline",
            [
                f"{constraints.baseline_artifact} is absent; unknown remains unknown. "
                "Write it with --write --reference <SHA> against pristine HEAD."
            ],
            "",
        )
    try:
        stored = load_baseline(artifact)
    except (OSError, ValueError, ConfigError, ToolFailure) as err:
        print(f"baseline: {err}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    reference_error = baseline_reference_error(stored, constraints.baseline_rev)
    if reference_error:
        return report("baseline", [reference_error], "")
    try:
        content_error = baseline_content_error(root, artifact, base)
    except ToolFailure as err:
        print(f"baseline: {err}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    if content_error:
        return report("baseline", [content_error], "")
    try:
        findings = collect_all_findings(root)
    except (OSError, ValueError, ConfigError, ToolFailure) as err:
        print(f"baseline: {err}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    recorded = constraints.baseline_rev
    try:
        errors = compare_findings(findings, stored, root=root, reference=recorded)
    except ToolFailure as err:
        print(f"baseline: {err}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    if base:
        try:
            sha = resolve_reference(root, base)
            if sha != recorded and base != recorded:
                errors.append(
                    f"baseline reference {recorded} does not match --base {base} ({sha}); "
                    "capture explicitly with --write --reference rather than swapping issues"
                )
        except ToolFailure as err:
            print(f"baseline: {err}", file=__import__("sys").stderr)
            return TOOL_FAILURE
    if errors:
        return report("baseline", errors, "")
    return report(
        "baseline",
        [],
        f"{len(findings)} findings match frozen {recorded or 'baseline'}",
    )


def run_baseline(
    root: Path,
    base: str | None,
    *,
    write: bool,
    reference: str | None,
) -> int:
    if write:
        if not reference:
            print(
                "baseline: --write requires --reference SHA of the pristine tree",
                file=__import__("sys").stderr,
            )
            return TOOL_FAILURE
        return write_baseline(root, reference)
    if reference and not write:
        print(
            "baseline: --reference is only valid with --write",
            file=__import__("sys").stderr,
        )
        return TOOL_FAILURE
    return compare_baseline(root, base)
