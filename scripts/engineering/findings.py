"""Stable finding fingerprints and baseline comparison."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gitutil import resolve_reference, run_git
from .tools import ToolFailure

BASELINE_SCHEMA = "dagent.engineering.baseline.v1"
BASELINE_KEYS = {
    "schema",
    "reference",
    "tool_identities",
    "counts",
    "findings",
}
FINDING_KEYS = {
    "fingerprint",
    "check",
    "path",
    "line",
    "detector",
    "severity",
    "metric",
}


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    line: int
    detector: str
    severity: str
    fingerprint: str
    detail: str
    metric: float | None = None
    # Collector-only evidence; never serialized into the frozen baseline.
    clone_identity: str | None = None
    clone_occurrences: (
        tuple[tuple[str, int, int, int | None, int | None], ...] | None
    ) = None
    clone_fragment: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "fingerprint": self.fingerprint,
            "check": self.check,
            "path": self.path,
            "line": self.line,
            "detector": self.detector,
            "severity": self.severity,
        }
        if self.metric is not None:
            payload["metric"] = self.metric
        return payload


def fingerprint_for(
    check: str,
    path: str,
    line: int,
    detector: str,
    identity: str,
    *,
    include_line: bool = True,
) -> str:
    line_part = str(line) if include_line else ""
    payload = f"{check}\0{path}\0{line_part}\0{detector}\0{identity}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def make_finding(
    *,
    check: str,
    path: str,
    line: int,
    detector: str,
    severity: str,
    identity: str,
    detail: str,
    metric: float | None = None,
    include_line: bool = True,
    clone_identity: str | None = None,
    clone_occurrences: tuple[tuple[str, int, int, int | None, int | None], ...]
    | None = None,
    clone_fragment: str | None = None,
) -> Finding:
    return Finding(
        check=check,
        path=path,
        line=line,
        detector=detector,
        severity=severity,
        fingerprint=fingerprint_for(
            check, path, line, detector, identity, include_line=include_line
        ),
        detail=detail,
        metric=metric,
        clone_identity=clone_identity,
        clone_occurrences=clone_occurrences,
        clone_fragment=clone_fragment,
    )


def counts_by_check(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.check] = counts.get(finding.check, 0) + 1
    return dict(sorted(counts.items()))


def _parse_baseline_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"baseline JSON is malformed: {exc.msg} at line {exc.lineno} column {exc.colno}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("baseline JSON must be an object")
    return data


def _validate_finding_item(item: object) -> None:
    if not isinstance(item, dict) or "fingerprint" not in item:
        raise ValueError("baseline finding is missing fingerprint")
    if "secret" in item or "match" in item or "raw" in item:
        raise ValueError("baseline must not store secret strings")
    unknown = sorted(set(item) - FINDING_KEYS)
    if unknown:
        raise ValueError(f"baseline finding has unapproved keys: {unknown}")


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    data = _parse_baseline_json(path)
    extra = sorted(set(data) - BASELINE_KEYS)
    if extra:
        raise ValueError(f"baseline has unapproved keys: {extra}")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ValueError("baseline JSON findings must be a list")
    for item in findings:
        _validate_finding_item(item)
    if data.get("schema") != BASELINE_SCHEMA:
        raise ValueError(f"baseline schema must be {BASELINE_SCHEMA!r}")
    return data


def baseline_reference_error(baseline: dict[str, Any], expected_rev: str) -> str | None:
    recorded = baseline.get("reference")
    if recorded != expected_rev:
        return (
            f"baseline reference {recorded!r} does not match "
            f"constraints.baseline.rev {expected_rev!r}; "
            "capture explicitly with --write --reference rather than swapping issues"
        )
    return None


def baseline_content_error(root: Path, artifact: Path, base: str | None) -> str | None:
    if base is None:
        return None
    sha = resolve_reference(root, base)
    try:
        relpath = artifact.relative_to(root).as_posix()
        artifact.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ToolFailure("baseline artifact must stay inside repository root") from exc
    if ".." in Path(relpath).parts:
        raise ToolFailure("baseline artifact must stay inside repository root")
    tracked = run_git(root, "ls-tree", "-r", "--name-only", sha, "--", relpath)
    if relpath not in tracked.splitlines():
        return None  # No committed artifact at this base, as during bootstrap.
    frozen = run_git(root, "rev-parse", f"{sha}:{relpath}").strip()
    current = run_git(root, "hash-object", "--no-filters", "--", str(artifact)).strip()
    if frozen != current:
        return (
            f"{relpath} differs from --base {base} ({sha}); "
            "frozen artifacts cannot be rewritten in a candidate"
        )
    return None


def baseline_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in data.get("findings", []):
        if isinstance(item, dict) and item.get("fingerprint"):
            index[str(item["fingerprint"])] = item
    return index


def _new_finding_error(finding: Finding) -> str:
    return (
        f"{finding.path}:{finding.line}  new {finding.check} finding "
        f"{finding.fingerprint} ({finding.detail}); new source must meet L3"
    )


def _severity_error(finding: Finding, previous: str) -> str:
    return (
        f"{finding.path}:{finding.line}  worsened {finding.check} finding "
        f"{finding.fingerprint} raised severity from {previous!r} to "
        f"{finding.severity!r}; an altered known issue cannot gain a new exemption"
    )


def _count_errors(current: list[Finding], baseline: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    recorded_counts = baseline.get("counts") or {}
    actual = counts_by_check(current)
    for check, ceiling in recorded_counts.items():
        if not isinstance(ceiling, int):
            errors.append(f"baseline count for {check} is not an integer")
            continue
        now = actual.get(check, 0)
        if now > ceiling:
            errors.append(
                f"{check} count rose from frozen {ceiling} to {now}; "
                "do not trade an old finding for a new one"
            )
    return errors


def _known_finding_errors(finding: Finding, known: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    previous = str(known.get("severity") or "")
    if _severity_rank(finding.severity) > _severity_rank(previous):
        errors.append(_severity_error(finding, previous))
    metric_error = _metric_increase(finding, known)
    if metric_error:
        errors.append(metric_error)
    return errors


def _clone_capacity(baseline: dict[str, Any]) -> Counter[str]:
    # The frozen list is a multiset, not an exemption per distinct hash.
    # Otherwise deletion of an unrelated clone finances arbitrarily many new
    # copies with a historically known pair/fragment fingerprint.
    return Counter(
        str(item["fingerprint"])
        for item in baseline.get("findings", [])
        if isinstance(item, dict)
        and item.get("check") == "duplicate_code"
        and item.get("detector") == "jscpd"
        and item.get("path") != "."
        and item.get("fingerprint")
    )


def _matching_baseline(
    finding: Finding, indexed: dict[str, dict[str, Any]], capacity: Counter[str]
) -> dict[str, Any] | None:
    clone = (
        finding.check == "duplicate_code"
        and finding.detector == "jscpd"
        and finding.path != "."
    )
    candidates = [finding.fingerprint]
    if finding.clone_identity and finding.clone_occurrences:
        # Older baselines fingerprinted the detector's first file. Its
        # order is arbitrary, but reverse and forward share one capacity.
        other = finding.clone_occurrences[1][0]
        reverse = fingerprint_for(
            finding.check,
            other,
            finding.line,
            finding.detector,
            finding.clone_identity,
            include_line=False,
        )
        if reverse != finding.fingerprint:
            candidates.append(reverse)
    match = next(
        (
            fingerprint
            for fingerprint in candidates
            if fingerprint in indexed and (not clone or capacity[fingerprint] > 0)
        ),
        None,
    )
    if match is None:
        return None
    if clone:
        capacity[match] -= 1
    return indexed[match]


def _lineage_errors(
    root: Path, reference: str, unmatched: list[Finding], current: list[Finding]
) -> list[str]:
    from .clone_lineage import inherited_clones

    eligible = [
        finding
        for finding in unmatched
        if finding.check == "duplicate_code" and finding.clone_occurrences
    ]
    inherited = (
        inherited_clones(root, reference, eligible, current) if eligible else set()
    )
    return [
        _new_finding_error(finding) for finding in unmatched if finding not in inherited
    ]


def compare_findings(
    current: list[Finding],
    baseline: dict[str, Any],
    *,
    root: Path | None = None,
    reference: str | None = None,
) -> list[str]:
    errors: list[str] = []
    indexed = baseline_index(baseline)
    capacity = _clone_capacity(baseline)
    unmatched: list[Finding] = []
    for finding in current:
        known = _matching_baseline(finding, indexed, capacity)
        if known is None:
            unmatched.append(finding)
        else:
            errors.extend(_known_finding_errors(finding, known))
    if root is not None and reference is not None:
        errors.extend(_lineage_errors(root, reference, unmatched, current))
    else:
        errors.extend(_new_finding_error(finding) for finding in unmatched)
    errors.extend(_count_errors(current, baseline))
    return errors


def _metric_increase(finding: Finding, known: dict[str, Any]) -> str | None:
    if finding.metric is None or "metric" not in known:
        return None
    previous = known.get("metric")
    if not isinstance(previous, (int, float)) or isinstance(previous, bool):
        return f"baseline metric for {finding.fingerprint} is not numeric"
    if finding.metric > previous:
        return (
            f"{finding.path}:{finding.line}  worsened {finding.check} finding "
            f"{finding.fingerprint} metric rose from {previous} to {finding.metric}; "
            "legacy debt may shrink but must not grow"
        )
    return None


def _severity_rank(value: str) -> int:
    order = {"info": 0, "low": 1, "medium": 2, "high": 3, "error": 4, "critical": 5}
    return order.get(value.lower(), 2)


def serialize_baseline(
    *,
    reference: str,
    tool_identities: dict[str, str],
    findings: list[Finding],
) -> dict[str, Any]:
    return {
        "schema": BASELINE_SCHEMA,
        "reference": reference,
        "tool_identities": tool_identities,
        "counts": counts_by_check(findings),
        "findings": [
            finding.as_dict()
            for finding in sorted(
                findings,
                key=lambda item: (
                    item.check,
                    item.path,
                    item.line,
                    item.detector,
                    item.fingerprint,
                    item.severity,
                    repr(item.metric),
                ),
            )
        ],
    }
