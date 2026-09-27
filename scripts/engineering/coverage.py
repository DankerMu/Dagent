"""Coverage gate over actual pytest and Vitest reports."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import ConfigError, load_constraints, relative_to_root
from .diffcheck import approved_offline_cleanup
from .findings import baseline_content_error
from .gitutil import list_source_at, resolve_reference, run_git, show_file
from .output import TOOL_FAILURE, report
from .tools import ToolFailure

PYTHON_ROOTS = ("src/xagent", "scripts/engineering")
FRONTEND_ROOT = "frontend/src"
PYTHON_SUFFIXES = {".py"}
FRONTEND_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}
FRONTEND_NON_SOURCE_NAMES = {"vitest.setup.ts", "vitest.setup.js"}
FRONTEND_TEST_SUFFIXES = (
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)


def _percent(covered: float, num_statements: float) -> float:
    if num_statements <= 0:
        return 100.0
    return 100.0 * covered / num_statements


def _load_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{label} report is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} report is malformed JSON: {exc.msg} at line {exc.lineno} column {exc.colno}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} report must be a JSON object")
    return payload


def _root_relative(
    root: Path, raw_path: str, *, label: str, frontend_relative: bool = False
) -> str:
    text = raw_path.strip()
    if text.startswith("file:"):
        parsed = urlparse(text)
        if parsed.scheme == "file":
            text = unquote(parsed.path)
    path = Path(text)
    root_resolved = root.resolve()
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(root_resolved).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"{label} path is outside the repository root: {raw_path}"
            ) from exc
    else:
        rel = path.as_posix()
        if rel.startswith("./"):
            rel = rel[2:]
        if frontend_relative and (rel.startswith("src/") or rel.startswith("public/")):
            rel = f"frontend/{rel}"
    return rel


def _pytest_files(payload: dict, root: Path) -> dict[str, dict[str, float]]:
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("pytest coverage JSON missing files object")
    result: dict[str, dict[str, float]] = {}
    for raw_path, body in files.items():
        if not isinstance(body, dict):
            raise ValueError(f"pytest coverage entry for {raw_path} must be an object")
        summary = body.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"pytest coverage summary missing for {raw_path}")
        rel = _root_relative(root, str(raw_path), label="pytest")
        covered = summary.get("covered_lines")
        num_statements = summary.get("num_statements")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (covered, num_statements)
        ):
            raise ValueError(f"pytest coverage {rel} requires nonnegative line counts")
        if covered > num_statements:
            raise ValueError(f"pytest coverage {rel} covered lines exceed statements")
        if summary.get("percent_covered") is not None:
            _finite_percent(rel, summary["percent_covered"], label="pytest coverage")
        result[rel] = {"lines": _percent(covered, num_statements)}
    return result


def _vitest_files(payload: dict, root: Path) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for raw_path, body in payload.items():
        if raw_path in {"total"}:
            continue
        if not isinstance(body, dict):
            raise ValueError(f"vitest coverage entry for {raw_path} must be an object")
        metrics: dict[str, float] = {}
        for name in ("lines", "statements", "branches", "functions"):
            block = body.get(name)
            if not isinstance(block, dict) or "pct" not in block:
                raise ValueError(f"vitest coverage {raw_path} missing {name}.pct")
            metrics[name] = _finite_percent(
                str(raw_path), block["pct"], label=f"vitest {name} coverage"
            )
        result[
            _root_relative(root, str(raw_path), label="vitest", frontend_relative=True)
        ] = metrics
    return result


def _is_frontend_non_source(path: Path) -> bool:
    name = path.name
    if name.endswith(".d.ts"):
        return True
    if name in FRONTEND_NON_SOURCE_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in FRONTEND_TEST_SUFFIXES)


def _inventory(
    root: Path, rel_root: str, suffixes: set[str], excludes: tuple[str, ...]
) -> list[str]:
    base = root / rel_root
    files: list[str] = []
    if not base.exists():
        return files
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if rel_root == FRONTEND_ROOT and _is_frontend_non_source(path):
            continue
        if path.suffix not in suffixes:
            continue
        rel = relative_to_root(root, path)
        if any(
            rel.startswith(item.rstrip("/") + "/") or rel == item.rstrip("/")
            for item in excludes
        ):
            continue
        files.append(rel)
    return sorted(files)


def _python_inventory(root: Path, excludes: tuple[str, ...]) -> list[str]:
    files: list[str] = []
    for rel_root in PYTHON_ROOTS:
        files.extend(_inventory(root, rel_root, PYTHON_SUFFIXES, excludes))
    return files


def _inventory_errors(
    python_inventory: list[str],
    frontend_inventory: list[str],
    py_files: dict[str, dict[str, float]],
    ts_files: dict[str, dict[str, float]],
) -> list[str]:
    errors: list[str] = []
    for rel in python_inventory:
        if rel not in py_files:
            errors.append(
                f"{rel}  missing from pytest coverage JSON; include unexecuted source "
                "instead of claiming a reduced-scope global"
            )
    for rel in frontend_inventory:
        alt = rel[len("frontend/") :] if rel.startswith("frontend/") else rel
        if (
            rel not in ts_files
            and alt not in ts_files
            and f"frontend/{alt}" not in ts_files
        ):
            errors.append(
                f"{rel}  missing from vitest coverage summary; include unexecuted source"
            )
    return errors


def _is_python_source(rel: str) -> bool:
    return rel.startswith("src/xagent/") or rel.startswith("scripts/engineering/")


def _historical_paths(root: Path, base: str | None) -> set[str] | None:
    if not base:
        return None
    return list_source_at(
        root,
        base,
        PYTHON_ROOTS + (FRONTEND_ROOT, "frontend/public"),
    )


def _finite_percent(rel: str, value: object, *, label: str = "measured floor") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} for {rel} is not a number")
    percent = float(value)
    if not math.isfinite(percent) or percent < 0.0 or percent > 100.0:
        raise ValueError(f"{label} for {rel} is out of range: {value!r}")
    return percent


def _load_measured_floors(
    path: Path | None,
    *,
    root: Path,
    base: str | None,
) -> tuple[dict[str, float], str | None]:
    if path is None:
        return {}, None
    path = root / path
    if not path.is_file():
        raise ValueError(f"measured coverage floors report is missing: {path}")
    if not base:
        raise ValueError("measured floors require --base so provenance can be bound")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("measured coverage floors report must be a JSON object")
    reference = payload.get("reference")
    source_hash = payload.get("source_hash")
    files = payload.get("files")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("measured floors require explicit reference provenance")
    if not isinstance(source_hash, str) or not source_hash.strip():
        raise ValueError("measured floors require explicit source_hash provenance")
    if not isinstance(files, dict):
        raise ValueError("measured floors files must be an object of path -> percent")
    recorded_ref = reference.strip()
    accepted = {base, resolve_reference(root, base)}
    if recorded_ref not in accepted:
        raise ValueError(
            f"measured floors reference {recorded_ref!r} does not match --base {base!r}"
        )
    expected_tree = run_git(root, "rev-parse", f"{base}^{{tree}}").strip()
    if source_hash.strip() != expected_tree:
        raise ValueError(
            "measured floors source_hash does not match the --base Git tree"
        )
    floors: dict[str, float] = {}
    for rel, value in files.items():
        floors[str(rel)] = _finite_percent(str(rel), value)
    return floors, f"{recorded_ref}#{source_hash.strip()}"


def _approved_offline_floor_change(root: Path, artifact: Path, base: str) -> bool:
    """Accept the separately authorized 72/75 -> 71/74 denominator adjustment."""
    relative = ".engineering/coverage-floors.json"
    if artifact != root / relative:
        return False
    previous = show_file(root, base, relative)
    if previous is None:
        return False
    # User-authorized artifact pair, not hashes supplied by the candidate manifest.
    old_digest = "13c94da7355a9fb12c068357785935a653bc257481afe7a62a53a4af6f984b59"  # pragma: allowlist secret - public artifact SHA256
    new_digest = "9b2715c76238f4c4ac44696e22167f073efd83c6f331c4d810cbfa4f5da4211f"  # pragma: allowlist secret - public artifact SHA256
    return (
        hashlib.sha256(previous.encode()).hexdigest() == old_digest
        and hashlib.sha256(artifact.read_bytes()).hexdigest() == new_digest
        and approved_offline_cleanup(root, base)
    )


def _check_floor_authority(
    root: Path, path: Path | None, base: str | None
) -> list[str]:
    if path is None:
        return []
    if not base:
        raise ValueError("measured floors require --artifact-base for content binding")
    error = baseline_content_error(root, root / path, base)
    if error and _approved_offline_floor_change(root, root / path, base):
        return []
    return [error] if error else []


def _required_line_floor(
    rel: str,
    new_floor: float,
    historical: set[str] | None,
    recorded: dict[str, float],
) -> tuple[float | None, str]:
    widget_rel = rel[len("frontend/") :] if rel.startswith("frontend/") else rel
    is_historical = historical is not None and (
        rel in historical or widget_rel in historical
    )
    if not is_historical:
        return new_floor, f"{int(new_floor)}% new-file floor"
    if rel in recorded:
        frozen = recorded[rel]
        return frozen, f"frozen historical floor {frozen:.1f}%"
    if widget_rel in recorded:
        frozen = recorded[widget_rel]
        return frozen, f"frozen historical floor {frozen:.1f}%"
    return None, (
        "historical file has no measured floor; unknown remains unknown "
        "(pass --measured-floors from the --base coverage report)"
    )


def _python_floor_errors(
    py_files: dict[str, dict[str, float]],
    new_floor: float,
    historical: set[str] | None,
    recorded: dict[str, float],
) -> list[str]:
    errors: list[str] = []
    for rel, metrics in py_files.items():
        if not _is_python_source(rel):
            continue
        required, label = _required_line_floor(rel, new_floor, historical, recorded)
        percent = metrics["lines"]
        if required is None:
            errors.append(f"{rel}  {label}")
            continue
        if percent + 1e-9 < required:
            errors.append(f"{rel}  line coverage {percent:.1f}% is below the {label}")
    return errors


def _floor_errors(
    py_files: dict[str, dict[str, float]],
    ts_files: dict[str, dict[str, float]],
    floor: float,
    historical: set[str] | None,
    recorded: dict[str, float],
) -> list[str]:
    errors = _python_floor_errors(py_files, floor, historical, recorded)
    for rel, metrics in ts_files.items():
        required, label = _required_line_floor(rel, floor, historical, recorded)
        percent = metrics.get("lines")
        if required is None:
            errors.append(f"{rel}  {label}")
            continue
        if percent is not None and percent + 1e-9 < required:
            errors.append(f"{rel}  line coverage {percent:.1f}% is below the {label}")
    return errors


def check_coverage(
    root: Path,
    pytest_json: Path | None,
    vitest_summary: Path | None,
    base: str | None = None,
    measured_floors: Path | None = None,
    scope: str = "all",
    artifact_base: str | None = None,
) -> int:
    try:
        constraints = load_constraints(root)
    except ConfigError as exc:
        print(f"coverage: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    need_python = scope in {"all", "python"}
    need_frontend = scope in {"all", "frontend"}
    missing: list[str] = []
    if need_python and pytest_json is None:
        missing.append("--pytest-json")
    if need_frontend and vitest_summary is None:
        missing.append("--vitest-summary")
    if missing:
        print(
            "coverage: "
            + " and ".join(missing)
            + " required for scope "
            + scope
            + "; a missing coverage report is not treated as 100%",
            file=__import__("sys").stderr,
        )
        return TOOL_FAILURE
    try:
        py_files = (
            _pytest_files(_load_json(pytest_json, "pytest"), root)
            if need_python
            else {}
        )
        ts_files = (
            _vitest_files(_load_json(vitest_summary, "vitest"), root)
            if need_frontend
            else {}
        )
        recorded, provenance = _load_measured_floors(
            measured_floors, root=root, base=base
        )
        authority_errors = _check_floor_authority(root, measured_floors, artifact_base)
        if authority_errors:
            return report("coverage", authority_errors, "")
        historical = _historical_paths(root, base)
        python_inventory = (
            _python_inventory(root, constraints.coverage_excludes)
            if need_python
            else []
        )
        frontend_inventory = (
            _inventory(
                root, FRONTEND_ROOT, FRONTEND_SUFFIXES, constraints.coverage_excludes
            )
            if need_frontend
            else []
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError, ToolFailure) as exc:
        print(f"coverage: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    errors = _inventory_errors(python_inventory, frontend_inventory, py_files, ts_files)
    errors.extend(
        _floor_errors(
            py_files,
            ts_files,
            float(constraints.min_line_coverage),
            historical,
            recorded,
        )
    )
    checked = len(python_inventory) + len(frontend_inventory)
    if errors:
        return report("coverage", errors, "")
    extra = (
        f", floors from {provenance}"
        if provenance
        else ", no historical floors (unknown not frozen)"
    )
    return report(
        "coverage",
        [],
        f"{checked} source files inventoried (scope {scope}), all floors hold{extra}",
    )
