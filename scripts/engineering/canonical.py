"""File size, naming, and scratch-directory checks."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Constraints, is_exempt, iter_files, relative_to_root
from .findings import Finding, make_finding

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
PHYSICAL_LINE_SUFFIXES = SOURCE_SUFFIXES | {".mjs", ".cjs"}


def collect_size_findings(root: Path, constraints: Constraints) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(root):
        if path.suffix not in PHYSICAL_LINE_SUFFIXES:
            continue
        rel = relative_to_root(root, path)
        if is_exempt(constraints, rel, "size_limits"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        count = len(lines)
        if count > constraints.max_file_lines:
            findings.append(
                make_finding(
                    check="size",
                    path=rel,
                    line=constraints.max_file_lines + 1,
                    detector="file-size",
                    severity="error",
                    identity="physical-lines",
                    metric=float(count),
                    detail=(
                        f"physical source lines {count} exceed "
                        f"{constraints.max_file_lines}"
                    ),
                )
            )
    return findings


def collect_naming_findings(root: Path, constraints: Constraints) -> list[Finding]:
    findings: list[Finding] = []
    compiled = [re.compile(pattern) for pattern in constraints.forbidden_suffixes]
    for path in iter_files(root):
        rel = relative_to_root(root, path)
        if is_exempt(constraints, rel, "naming"):
            continue
        components = (*Path(rel).parts[:-1], *path.stem.split("."))
        matched = None
        for pattern in compiled:
            if any(
                match.end() == len(component)
                for component in components
                for match in pattern.finditer(component)
            ):
                matched = pattern.pattern
                break
        if matched is None:
            continue
        findings.append(
            make_finding(
                check="naming",
                path=rel,
                line=1,
                detector="naming",
                severity="error",
                identity=matched,
                detail=f"forbidden naming suffix matching {matched}",
            )
        )
    return findings


def collect_scratch_findings(root: Path, constraints: Constraints) -> list[Finding]:
    findings: list[Finding] = []
    names = {item.strip("/").lower() for item in constraints.scratchpad_directories}
    for path in iter_files(root):
        rel = relative_to_root(root, path)
        parts = {part.lower() for part in Path(rel).parts}
        hit = parts & names
        if not hit:
            continue
        if is_exempt(constraints, rel, "scratchpad"):
            continue
        directory = next(iter(sorted(hit)))
        findings.append(
            make_finding(
                check="scratchpad",
                path=rel,
                line=1,
                detector="scratchpad",
                severity="error",
                identity=directory,
                detail=f"scratchpad directory {directory}",
            )
        )
    return findings
