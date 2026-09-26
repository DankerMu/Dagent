"""Load constraints.yaml with fail-closed parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "3.6.0"
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    ".run",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "htmlcov",
    "coverage",
    "frontend_dist",
    ".omp",
    ".engineering",
}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}


class ConfigError(ValueError):
    """A required constraint is missing or not the declared type."""


def _walk(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"constraints.yaml missing required key {dotted}")
        current = current[part]
    return current


def require_int(data: dict[str, Any], dotted: str) -> int:
    value = _walk(data, dotted)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{dotted} must be an integer, got {value!r}")
    return value


def require_str(data: dict[str, Any], dotted: str) -> str:
    value = _walk(data, dotted)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{dotted} must be a non-empty string, got {value!r}")
    return value


def require_list(data: dict[str, Any], dotted: str) -> list[Any]:
    value = _walk(data, dotted)
    if not isinstance(value, list):
        raise ConfigError(f"{dotted} must be a list, got {value!r}")
    return value


def require_dict(data: dict[str, Any], dotted: str) -> dict[str, Any]:
    value = _walk(data, dotted)
    if not isinstance(value, dict):
        raise ConfigError(f"{dotted} must be a mapping, got {value!r}")
    return value


@dataclass(frozen=True)
class Constraints:
    raw: dict[str, Any]
    schema_version: str
    profile_level: str
    max_file_lines: int
    max_complexity: int
    max_function_lines: int
    max_pr_diff_lines: int
    min_line_coverage: int
    coverage_scope: str
    duplicate_percent: int
    dead_code_confidence: int
    forbidden_suffixes: tuple[str, ...]
    scratchpad_directories: tuple[str, ...]
    agents_md_max_lines: int
    baseline_artifact: str
    baseline_rev: str
    verification_commands: tuple[str, ...]
    generated_section_titles: tuple[str, ...]
    exemption_entries: tuple[dict[str, Any], ...]
    coverage_excludes: tuple[str, ...]
    known_limitation_files: tuple[str, ...]
    ratchet_owner: str
    ratchet_target: str


def _verification_commands(loaded: dict[str, Any]) -> list[str]:
    surfaces = require_dict(loaded, "verification.surfaces")
    commands: list[str] = []
    for name, body in surfaces.items():
        if not isinstance(body, dict) or "command" not in body:
            raise ConfigError(f"verification.surfaces.{name}.command is required")
        command = body["command"]
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"verification.surfaces.{name}.command must be a string")
        commands.append(command.strip())
    return commands


def _generated_titles(loaded: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for item in require_list(loaded, "generated_sections.agents_md"):
        if not isinstance(item, dict) or not item.get("title"):
            raise ConfigError("generated_sections.agents_md entries need title")
        titles.append(str(item["title"]))
    return titles


def _exemptions(loaded: dict[str, Any]) -> list[dict[str, Any]]:
    exemptions = require_list(loaded, "exemptions.entries")
    parsed: list[dict[str, Any]] = []
    for entry in exemptions:
        if not isinstance(entry, dict):
            raise ConfigError("exemptions.entries must be mappings")
        for key in ("path", "rules", "reason"):
            if not entry.get(key):
                raise ConfigError(f"exemption is missing {key}")
        parsed.append(entry)
    return parsed


def load_constraints(root: Path) -> Constraints:
    path = root / "constraints.yaml"
    if not path.is_file():
        raise ConfigError("constraints.yaml is missing")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigError("constraints.yaml must be a mapping")
    schema = require_str(loaded, "strictness_profile.schema_version")
    if schema != SCHEMA_VERSION:
        raise ConfigError(
            f"strictness_profile.schema_version expected {SCHEMA_VERSION!r}, got {schema!r}"
        )
    scratch = require_list(loaded, "code_canonicality.scratchpad_directories.paths")
    suffixes = require_list(loaded, "code_canonicality.forbidden_suffixes.patterns")
    if any(not isinstance(item, str) or not item for item in scratch + suffixes):
        raise ConfigError("forbidden suffix and scratchpad entries must be strings")
    return Constraints(
        raw=loaded,
        schema_version=schema,
        profile_level=require_str(loaded, "strictness_profile.level"),
        max_file_lines=require_int(loaded, "size_limits.max_file_lines.value"),
        max_complexity=require_int(loaded, "size_limits.max_complexity.value"),
        max_function_lines=require_int(loaded, "size_limits.max_function_lines.value"),
        max_pr_diff_lines=require_int(loaded, "size_limits.max_pr_diff_lines.value"),
        min_line_coverage=require_int(loaded, "testing.min_line_coverage.value"),
        coverage_scope=require_str(loaded, "testing.min_line_coverage.scope"),
        duplicate_percent=require_int(
            loaded, "anti_drift.duplicate_code_threshold_percent.value"
        ),
        dead_code_confidence=require_int(
            loaded, "anti_drift.dead_code_min_confidence.value"
        ),
        forbidden_suffixes=tuple(suffixes),
        scratchpad_directories=tuple(scratch),
        agents_md_max_lines=require_int(loaded, "documentation.agents_md_max_lines"),
        baseline_artifact=require_str(loaded, "baseline.artifact"),
        baseline_rev=require_str(loaded, "baseline.rev"),
        verification_commands=tuple(_verification_commands(loaded)),
        generated_section_titles=tuple(_generated_titles(loaded)),
        exemption_entries=tuple(_exemptions(loaded)),
        coverage_excludes=tuple(
            str(item) for item in require_list(loaded, "testing.inventory_excludes")
        ),
        known_limitation_files=tuple(
            str(item)
            for item in require_list(
                loaded, "documentation.known_limitations.required_in"
            )
        ),
        ratchet_owner=require_str(loaded, "baseline.ratchet.owner"),
        ratchet_target=require_str(loaded, "baseline.ratchet.next_milestone_target"),
    )


def is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigError(
            f"source path escapes repository or cannot resolve: {path}"
        ) from exc


def is_exempt(constraints: Constraints, relpath: str, rule: str) -> bool:
    for entry in constraints.exemption_entries:
        target = str(entry["path"]).rstrip("/")
        rules = entry.get("rules") or []
        if rule not in rules and "all" not in rules:
            continue
        if relpath == target or relpath.startswith(f"{target}/"):
            return True
    return False


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = [
            name
            for name in dirs
            if not is_skipped_dir(name)
            and (parent / name).relative_to(root).as_posix()
            not in {"build", "frontend/out"}
            and not (parent / name).is_symlink()
        ]
        files.extend(parent / name for name in names if (parent / name).is_file())
    return files


def iter_source_files(root: Path) -> list[Path]:
    return [path for path in iter_files(root) if path.suffix in SOURCE_SUFFIXES]
