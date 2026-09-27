"""Adapters for existing L3 detectors."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path

from .clone_lineage import clone_span
from .config import Constraints, is_exempt, relative_to_root
from .findings import Finding, make_finding
from .gitutil import list_scannable_files
from .source_anchor import line_digest, next_occurrence, span_digest
from .tools import (
    ToolFailure,
    parse_json_output,
    require_executable,
    require_frontend_bin,
    run_command,
)

LIZARD_FIELDS = (
    "NLOC",
    "CCN",
    "token_count",
    "param_count",
    "length",
    "location",
    "file",
    "function",
    "signature",
    "start",
    "end",
)
JSCPD_SCAN_ROOTS = (
    "src",
    "frontend/src",
    "frontend/public/widget.js",
    "scripts/engineering",
)


def tool_identity(root: Path, executable: str) -> str:
    result = run_command([executable, "--version"], cwd=root, timeout=30)
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if text else executable


def _rel(root: Path, raw: str) -> str:
    cleaned = raw.strip().strip('"').strip("'")
    path = Path(cleaned)
    if path.is_absolute():
        try:
            return relative_to_root(root, path)
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _int_field(row: dict[str, str], name: str, raw: str) -> int:
    try:
        return int(float(row[name]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolFailure(f"lizard produced a non-numeric CSV row: {raw!r}") from exc


def _lizard_row(line: str) -> dict[str, str]:
    rows = list(csv.reader(io.StringIO(line)))
    if not rows:
        raise ToolFailure(f"lizard produced a malformed CSV row: {line!r}")
    parts = rows[0]
    if len(parts) != len(LIZARD_FIELDS):
        raise ToolFailure(f"lizard produced a malformed CSV row: {line!r}")
    return dict(zip(LIZARD_FIELDS, parts, strict=True))


def _lizard_findings(
    root: Path,
    constraints: Constraints,
    row: dict[str, str],
    raw: str,
) -> list[Finding]:
    complexity = _int_field(row, "CCN", raw)
    length = _int_field(row, "length", raw)
    start = _int_field(row, "start", raw)
    name = row["function"].strip() or "unknown"
    path = _rel(root, row["file"])
    if is_exempt(constraints, path, "size_limits"):
        return []
    findings: list[Finding] = []
    if complexity > constraints.max_complexity:
        findings.append(
            make_finding(
                check="complexity",
                path=path,
                line=start,
                detector="lizard",
                severity="error",
                identity=row["identity"],
                metric=float(complexity),
                include_line=False,
                detail=(
                    f"cyclomatic complexity {complexity} exceeds "
                    f"{constraints.max_complexity} in {name}"
                ),
            )
        )
    if length > constraints.max_function_lines:
        findings.append(
            make_finding(
                check="function_size",
                path=path,
                line=start,
                detector="lizard",
                severity="error",
                identity=row["identity"],
                metric=float(length),
                include_line=False,
                detail=(
                    f"function length {length} exceeds "
                    f"{constraints.max_function_lines} in {name}"
                ),
            )
        )
    return findings


def _lizard_symbol(
    root: Path, row: dict[str, str], sources: dict[str, list[str]]
) -> str:
    name = row["function"]
    if name != "(anonymous)":
        return name
    path = _rel(root, row["file"])
    try:
        if path not in sources:
            sources[path] = (root / path).read_text(encoding="utf-8").splitlines()
        header = sources[path][int(row["start"]) - 1].strip()
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        raise ToolFailure(f"cannot identify anonymous function in {path}") from exc
    return f"{name}:{hashlib.sha256(header.encode()).hexdigest()}"


def parse_lizard_csv(
    root: Path, constraints: Constraints, stdout: str
) -> list[Finding]:
    findings: list[Finding] = []
    occurrences: dict[tuple[str, str], int] = {}
    sources: dict[str, list[str]] = {}
    for line in stdout.splitlines():
        if not line.strip() or line.lower().startswith("nloc"):
            continue
        row = _lizard_row(line)
        name = _lizard_symbol(root, row, sources)
        row["identity"] = next_occurrence(occurrences, _rel(root, row["file"]), name)
        findings.extend(_lizard_findings(root, constraints, row, line))
    return findings


def parse_vulture_output(
    root: Path, constraints: Constraints, stdout: str
) -> list[Finding]:
    findings: list[Finding] = []
    sources: dict[str, str] = {}
    occurrences: dict[tuple[str, str], int] = {}
    pattern = re.compile(r"^(?P<path>.+):(?P<line>\d+):\s+(?P<detail>.+)$")
    for line in stdout.splitlines():
        match = pattern.match(line.strip())
        if not match:
            if line.strip():
                raise ToolFailure(f"vulture produced a malformed finding: {line!r}")
            continue
        path = _rel(root, match.group("path"))
        if is_exempt(constraints, path, "dead_code"):
            continue
        detail = match.group("detail").strip()
        lineno = int(match.group("line"))
        semantic = f"{detail}:{line_digest(root, path, lineno, sources)}"
        findings.append(
            make_finding(
                check="dead_code",
                path=path,
                line=lineno,
                detector="vulture",
                severity="error",
                identity=next_occurrence(occurrences, path, semantic),
                include_line=False,
                detail=detail,
            )
        )
    return findings


def _frontend_path(raw: object) -> str:
    text = str(raw)
    if text.startswith("frontend/"):
        return text
    return f"frontend/{text}"


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ToolFailure(label)
    return value


KNIP_ISSUE_KEYS = {
    "file",
    "binaries",
    "catalog",
    "catalogReferences",
    "dependencies",
    "devDependencies",
    "duplicates",
    "enumMembers",
    "exports",
    "files",
    "namespaceMembers",
    "optionalPeerDependencies",
    "types",
    "unlisted",
    "unresolved",
}
KNIP_SYMBOL_FIELDS = (
    "exports",
    "types",
    "enumMembers",
    "namespaceMembers",
)
KNIP_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "optionalPeerDependencies",
    "unlisted",
    "unresolved",
    "binaries",
    "catalog",
    "catalogReferences",
    "duplicates",
)


def _knip_file_finding(path: str) -> Finding:
    return make_finding(
        check="dead_code",
        path=path,
        line=1,
        detector="knip",
        severity="error",
        identity="unused-file",
        detail="unused TypeScript file",
    )


def _knip_named_finding(path: str, scope: str, item: object) -> Finding:
    if not isinstance(item, dict):
        raise ToolFailure(f"knip {scope} finding must be an object")
    symbol = str(item.get("name") or item.get("identifier") or scope)
    label = "export" if scope == "exports" else scope
    return make_finding(
        check="dead_code",
        path=path,
        line=int(item.get("line") or 1),
        detector="knip",
        severity="error",
        identity=f"{scope}:{symbol}",
        include_line=False,
        detail=f"unused {label} {symbol}",
    )


def _knip_issue_object(item: object) -> dict[str, object]:
    if not isinstance(item, dict):
        raise ToolFailure("knip issue must be an object")
    unknown = sorted(set(item) - KNIP_ISSUE_KEYS)
    if unknown:
        raise ToolFailure(f"knip issue has unknown fields: {unknown}")
    return item


def _knip_named_list(issue: dict[str, object], field: str) -> list[object]:
    value = issue.get(field) or []
    return _require_list(value, f"knip {field} must be a list")


def _knip_unused_file_entries(issue: dict[str, object], path: str) -> list[Finding]:
    findings: list[Finding] = []
    for entry in _knip_named_list(issue, "files"):
        if not isinstance(entry, dict):
            raise ToolFailure("knip files finding must be an object")
        findings.append(_knip_file_finding(path))
    return findings


def _knip_scoped_entries(
    issue: dict[str, object], path: str, fields: tuple[str, ...]
) -> list[Finding]:
    findings: list[Finding] = []
    for field in fields:
        entries = _knip_named_list(issue, field)
        if field == "duplicates":
            entries = [
                member
                for group in entries
                for member in _require_list(
                    group, "knip duplicate group must be a list"
                )
            ]
        for entry in entries:
            findings.append(_knip_named_finding(path, field, entry))
    return findings


def _knip_issue_findings(constraints: Constraints, item: object) -> list[Finding]:
    issue = _knip_issue_object(item)
    path = _frontend_path(issue.get("file") or "")
    findings = _knip_unused_file_entries(issue, path)
    findings.extend(_knip_scoped_entries(issue, path, KNIP_SYMBOL_FIELDS))
    findings.extend(_knip_scoped_entries(issue, path, KNIP_DEPENDENCY_FIELDS))
    return [
        finding
        for finding in findings
        if not is_exempt(constraints, finding.path, "dead_code")
    ]


def parse_knip_payload(
    root: Path, constraints: Constraints, payload: object
) -> list[Finding]:
    if not isinstance(payload, dict):
        raise ToolFailure("knip JSON must be an object")
    extra = sorted(set(payload) - {"issues"})
    if extra:
        raise ToolFailure(f"knip JSON has unknown fields: {extra}")
    issues = payload.get("issues")
    findings: list[Finding] = []
    for item in _require_list(issues, "knip JSON issues must be a list"):
        findings.extend(_knip_issue_findings(constraints, item))
    return findings


def _jscpd_scan_roots(root: Path) -> tuple[Path, ...]:
    return tuple(root / rel for rel in JSCPD_SCAN_ROOTS)


def _jscpd_matches(root: Path, path: Path) -> list[str]:
    return [
        relative_to_root(root, candidate / path)
        for candidate in _jscpd_scan_roots(root)
        if (candidate / path).is_file()
    ]


def _jscpd_rel(root: Path, raw: str) -> str:
    cleaned = raw.strip().strip('"').strip("'")
    if not cleaned:
        return ""
    path = Path(cleaned)
    if path.is_absolute():
        return _rel(root, cleaned)
    if (root / path).is_file() or cleaned.startswith(("src/", "frontend/", "scripts/")):
        return path.as_posix()
    matches = _jscpd_matches(root, path)
    if not matches:
        return path.as_posix()
    if len(matches) > 1:
        raise ToolFailure(
            f"jscpd path {cleaned!r} is ambiguous across scan roots: {matches}"
        )
    return matches[0]


def _clone_start(first: dict[str, object]) -> int:
    start_loc = first.get("startLoc")
    start_from_loc = start_loc.get("line") if isinstance(start_loc, dict) else None
    return int(first.get("start") or start_from_loc or 1)


def _clone_pair_identity(left: str, right: str, fragment: str) -> str:
    pair = "->".join(sorted((left, right)))
    digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()[:32]
    return f"{pair}:{digest}"


def _jscpd_percent(total: object) -> float:
    if not isinstance(total, dict) or "percentage" not in total:
        raise ToolFailure("jscpd statistics.total.percentage is required")
    percentage = total["percentage"]
    if isinstance(percentage, bool) or not isinstance(percentage, (int, float)):
        raise ToolFailure("jscpd percentage must be numeric")
    value = float(percentage)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ToolFailure("jscpd percentage must be finite and between 0 and 100")
    return value


def _jscpd_rate_finding(percent_value: float, limit: int) -> Finding:
    return make_finding(
        check="duplicate_code",
        path=".",
        line=1,
        detector="jscpd",
        severity="error",
        identity="aggregate-rate",
        metric=percent_value,
        include_line=False,
        detail=f"duplication {percent_value}% exceeds {limit}%",
    )


def _clone_files(
    clone: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    first = clone.get("firstFile") or clone.get("a") or {}
    second = clone.get("secondFile") or clone.get("b") or {}
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise ToolFailure("jscpd clone files must be objects")
    return first, second


def _clone_fragment(clone: dict[str, object]) -> str:
    fragment = clone.get("fragment")
    if not isinstance(fragment, str) or not fragment:
        raise ToolFailure("jscpd clone is missing fragment")
    return fragment


def _clone_pair(
    root: Path, clone: dict[str, object]
) -> tuple[str, str, dict[str, object], dict[str, object]]:
    first, second = _clone_files(clone)
    left = _jscpd_rel(root, str(first.get("name") or first.get("path") or ""))
    right = _jscpd_rel(root, str(second.get("name") or second.get("path") or ""))
    return left, right, first, second


def _jscpd_clone_finding(
    root: Path, constraints: Constraints, clone: object
) -> Finding | None:
    if not isinstance(clone, dict):
        raise ToolFailure("jscpd clone entry must be an object")
    left, right, first, second = _clone_pair(root, clone)
    if is_exempt(constraints, left, "duplicate_code") and is_exempt(
        constraints, right, "duplicate_code"
    ):
        return None
    fragment = _clone_fragment(clone)
    identity = _clone_pair_identity(left, right, fragment)
    return make_finding(
        check="duplicate_code",
        path=left,
        line=_clone_start(first),
        detector="jscpd",
        severity="error",
        identity=identity,
        include_line=False,
        detail=f"duplicated block also in {right}",
        clone_identity=identity,
        clone_occurrences=((left, *clone_span(first)), (right, *clone_span(second))),
        clone_fragment=fragment,
    )


def _jscpd_clones(payload: dict[str, object]) -> list[object]:
    clones = payload.get("duplicates")
    if not isinstance(clones, list):
        raise ToolFailure("jscpd clones must be a list")
    return clones


def parse_jscpd_payload(
    root: Path, constraints: Constraints, payload: object
) -> list[Finding]:
    if not isinstance(payload, dict):
        raise ToolFailure("jscpd JSON must be an object")
    stats = payload.get("statistics") or {}
    total = stats.get("total") if isinstance(stats, dict) else {}
    findings: list[Finding] = []
    percent_value = _jscpd_percent(total)
    if percent_value > constraints.duplicate_percent:
        findings.append(
            _jscpd_rate_finding(percent_value, constraints.duplicate_percent)
        )
    findings.extend(
        finding
        for clone in _jscpd_clones(payload)
        if (finding := _jscpd_clone_finding(root, constraints, clone)) is not None
    )
    return findings


def _semgrep_rule_id(check_id: str) -> str:
    text = check_id.strip()
    marker = ".semgrep.yml."
    if marker in text:
        return text.rsplit(marker, 1)[-1]
    return text


def _semgrep_error_location(item: dict[str, object]) -> str:
    location = item.get("path") or item.get("span") or item.get("location")
    if isinstance(location, str):
        return location
    if not isinstance(location, dict):
        return ""
    path = str(location.get("file") or location.get("path") or "")
    line = location.get("line") or location.get("start")
    if not line:
        return path
    return f"{path}:{line}" if path else str(line)


def _semgrep_error_summary(errors: list[object]) -> str:
    summaries: list[str] = []
    for item in errors[:5]:
        if not isinstance(item, dict):
            summaries.append(type(item).__name__)
            continue
        code = str(item.get("code") or item.get("type") or "error")
        summaries.append(f"{code} {_semgrep_error_location(item)}".strip())
    extra = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
    return f"semgrep reported {len(errors)} tool errors: {'; '.join(summaries)}{extra}"


def _semgrep_extra(item: dict[str, object]) -> dict[str, object]:
    extra = item.get("extra")
    return extra if isinstance(extra, dict) else {}


def _semgrep_result_finding(
    root: Path,
    constraints: Constraints,
    item: object,
    sources: dict[str, str],
    occurrences: dict[tuple[str, str], int],
) -> Finding | None:
    if not isinstance(item, dict):
        raise ToolFailure("semgrep result must be an object")
    extra = _semgrep_extra(item)
    path = _rel(root, str(item.get("path") or ""))
    if is_exempt(constraints, path, "sast"):
        return None
    start = item.get("start")
    end = item.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise ToolFailure("semgrep result is missing coordinates")
    check_id = _semgrep_rule_id(
        str(item.get("check_id") or extra.get("fingerprint") or "semgrep")
    )
    semantic = f"{check_id}:{span_digest(root, path, start, end, sources)}"
    return make_finding(
        check="sast",
        path=path,
        line=int(start.get("line") or 1),
        detector="semgrep",
        severity=str(extra.get("severity") or "error").lower(),
        identity=next_occurrence(occurrences, path, semantic),
        include_line=False,
        detail=str(extra.get("message") or check_id),
    )


def _semgrep_results(payload: dict[str, object]) -> list[object]:
    errors = payload.get("errors") or []
    if errors:
        if not isinstance(errors, list):
            raise ToolFailure("semgrep reported tool errors")
        raise ToolFailure(_semgrep_error_summary(errors))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ToolFailure("semgrep JSON results must be a list")
    return results


def parse_semgrep_payload(
    root: Path, constraints: Constraints, payload: object
) -> list[Finding]:
    if not isinstance(payload, dict):
        raise ToolFailure("semgrep JSON must be an object")
    findings: list[Finding] = []
    sources: dict[str, str] = {}
    occurrences: dict[tuple[str, str], int] = {}
    for item in _semgrep_results(payload):
        finding = _semgrep_result_finding(root, constraints, item, sources, occurrences)
        if finding is not None:
            findings.append(finding)
    return findings


def _secret_hash(rel: str, detector: str, hashed: object) -> str:
    text = str(hashed or "")
    if text:
        return text
    return hashlib.sha256(f"{rel}:{detector}".encode("utf-8")).hexdigest()


def _secret_finding(rel: str, item: object) -> Finding:
    if not isinstance(item, dict):
        raise ToolFailure("detect-secrets finding must be an object")
    if "secret" in item or "raw" in item:
        raise ToolFailure("detect-secrets payload must not include secret strings")
    line = int(item.get("line_number") or 1)
    detector = str(item.get("type") or "Secret")
    hashed = _secret_hash(rel, detector, item.get("hashed_secret"))
    return make_finding(
        check="secret",
        path=rel,
        line=line,
        detector="detect-secrets",
        severity="error",
        identity=f"{detector}:{hashed}",
        include_line=False,
        detail=f"{detector} at {rel}:{line}",
    )


def _secret_items(rel: str, items: object) -> list[object]:
    if not isinstance(items, list):
        raise ToolFailure(f"detect-secrets results for {rel} must be a list")
    return items


def parse_detect_secrets_payload(
    root: Path, constraints: Constraints, payload: object
) -> list[Finding]:
    if not isinstance(payload, dict):
        raise ToolFailure("detect-secrets JSON must be an object")
    results = payload.get("results")
    if not isinstance(results, dict):
        raise ToolFailure("detect-secrets results must be an object")
    findings: list[Finding] = []
    for path, items in results.items():
        rel = _rel(root, str(path))
        if rel == constraints.baseline_artifact or is_exempt(
            constraints, rel, "secrets"
        ):
            continue
        findings.extend(
            _secret_finding(rel, item) for item in _secret_items(rel, items)
        )
    return findings


def collect_lizard(root: Path, constraints: Constraints) -> list[Finding]:
    exe = require_executable("lizard")
    argv = [
        exe,
        "-C",
        str(constraints.max_complexity),
        "-L",
        str(constraints.max_function_lines),
        "-l",
        "python",
        "-l",
        "javascript",
        "-l",
        "typescript",
        "--csv",
        "--exclude",
        "*/frontend_dist/*",
        "--exclude",
        "*/uploads/*",
        str(root / "src"),
        str(root / "frontend" / "src"),
        str(root / "frontend" / "public" / "widget.js"),
        str(root / "scripts" / "engineering"),
    ]
    result = run_command(argv, cwd=root, timeout=180)
    if result.returncode not in (0, 1):
        raise ToolFailure(
            f"lizard failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return parse_lizard_csv(root, constraints, result.stdout)


def collect_vulture(root: Path, constraints: Constraints) -> list[Finding]:
    exe = require_executable("vulture")
    argv = [
        exe,
        "src",
        *(["scripts/engineering"] if (root / "scripts/engineering").is_dir() else []),
        "--min-confidence",
        str(constraints.dead_code_confidence),
    ]
    result = run_command(argv, cwd=root, timeout=180)
    if result.returncode not in (0, 3):
        raise ToolFailure(
            f"vulture failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return parse_vulture_output(root, constraints, result.stdout)


def collect_knip(root: Path, constraints: Constraints) -> list[Finding]:
    frontend = root / "frontend"
    exe = require_frontend_bin(root, "knip")
    argv = [exe, "--reporter", "json", "--no-progress"]
    result = run_command(argv, cwd=frontend, timeout=180)
    if result.returncode not in (0, 1):
        raise ToolFailure(
            f"knip failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    payload = parse_json_output(result)
    return parse_knip_payload(root, constraints, payload)


def collect_jscpd(root: Path, constraints: Constraints) -> list[Finding]:
    exe = require_frontend_bin(root, "jscpd")
    argv = [
        exe,
        "src",
        "frontend/src",
        "frontend/public/widget.js",
        *(["scripts/engineering"] if (root / "scripts/engineering").is_dir() else []),
        "--absolute",
        "--min-lines",
        "5",
        "--ignore",
        "**/frontend_dist/**,**/uploads/**",
        "--threshold",
        str(constraints.duplicate_percent),
        "--reporters",
        "json",
        "--silent",
        "--output",
        str(root / ".run" / "jscpd"),
    ]
    result = run_command(argv, cwd=root, timeout=180)
    if result.returncode not in (0, 1):
        raise ToolFailure(
            f"jscpd failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    report_path = root / ".run" / "jscpd" / "jscpd-report.json"
    if not report_path.is_file():
        text = result.stdout.strip()
        if not text:
            raise ToolFailure("jscpd produced no JSON report")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolFailure(f"jscpd produced malformed JSON: {exc.msg}") from exc
    else:
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolFailure(f"jscpd report is malformed JSON: {exc.msg}") from exc
    return parse_jscpd_payload(root, constraints, payload)


def _semgrep_failure_text(result) -> str:
    stderr = result.stderr.strip()
    return stderr or "semgrep failed without a diagnostic"


def collect_semgrep(root: Path, constraints: Constraints) -> list[Finding]:
    exe = require_executable("semgrep")
    config = root / ".semgrep.yml"
    if not config.is_file():
        raise ToolFailure(".semgrep.yml is missing")
    argv = [
        exe,
        "--config",
        str(config),
        "--json",
        "--quiet",
        "--metrics",
        "off",
        "--no-rewrite-rule-ids",
        "src",
        "frontend/src",
        *(["scripts/engineering"] if (root / "scripts/engineering").is_dir() else []),
    ]
    result = run_command(argv, cwd=root, timeout=240)
    if result.returncode not in (0, 1):
        raise ToolFailure(
            f"semgrep failed (exit {result.returncode}): {_semgrep_failure_text(result)}"
        )
    payload = parse_json_output(result)
    return parse_semgrep_payload(root, constraints, payload)


def collect_detect_secrets(root: Path, constraints: Constraints) -> list[Finding]:
    exe = require_executable("detect-secrets")
    artifact = constraints.baseline_artifact
    files = [rel for rel in list_scannable_files(root) if rel != artifact]
    if not files:
        return []
    argv = [exe, "scan", "--force-use-all-plugins", *files]
    result = run_command(argv, cwd=root, timeout=180)
    if result.returncode not in (0, 1):
        raise ToolFailure(
            f"detect-secrets failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    payload = parse_json_output(result)
    return parse_detect_secrets_payload(root, constraints, payload)
