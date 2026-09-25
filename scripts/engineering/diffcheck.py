"""PR diff-size gate with a content-bound, single-base bootstrap approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import ConfigError, load_constraints
from .gitutil import diff_numstat, porcelain_entries, resolve_reference, show_file
from .output import TOOL_FAILURE, report
from .tools import ToolFailure

BOOTSTRAP_APPROVAL_PATH = ".engineering/bootstrap-approval.json"


def approved_bootstrap(root: Path, base: str, files: list[str]) -> bool:
    """Accept only the exact approved snapshot before the first bootstrap merge."""
    approval_path = BOOTSTRAP_APPROVAL_PATH
    path = root / approval_path
    if not path.is_file():
        return False
    try:
        approval = json.loads(path.read_text(encoding="utf-8"))
        if set(approval) != {"base", "scope", "files"}:
            return False
        if approval["scope"] != "user-approved initial engineering bootstrap only":
            return False
        if resolve_reference(root, base) != approval["base"]:
            return False
        # Once the approval has landed, no subsequent PR can reuse it.
        if show_file(root, base, approval_path) is not None:
            return False
        expected = approval["files"]
        if not isinstance(expected, dict):
            return False
        actual_files = set(files) - {approval_path}
        if actual_files != set(expected):
            return False
        for rel in actual_files:
            source = root / rel
            if source.is_symlink() or not source.is_file():
                return False
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if expected[rel] != digest:
                return False
        return True
    except (OSError, ValueError, TypeError, ToolFailure):
        return False


def diff_inventory(root: Path, base: str) -> tuple[int, list[str]]:
    """Count tracked text and retain every changed file, including binaries."""
    total, files = diff_numstat(root, base)
    for status, rel in porcelain_entries(root):
        if not status.startswith("?"):
            continue
        path = root / rel
        if not path.is_file():
            continue
        files.append(rel)
        try:
            total += len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    return total, files


def check_diff(root: Path, base: str | None) -> int:
    if not base:
        print(
            "diff: --base is required, including for bootstrap approval",
            file=__import__("sys").stderr,
        )
        return TOOL_FAILURE
    try:
        constraints = load_constraints(root)
        total, counted_files = diff_inventory(root, base)
    except ConfigError as exc:
        print(f"diff: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    except ToolFailure as exc:
        print(f"diff: {exc}", file=__import__("sys").stderr)
        return TOOL_FAILURE
    errors: list[str] = []
    limit = constraints.max_pr_diff_lines
    if total > limit:
        if approved_bootstrap(root, base, counted_files):
            return report(
                "diff",
                [],
                f"{total} changed lines: exact user-approved bootstrap snapshot; "
                f"subsequent PR limit remains {limit}",
            )
        preview = ", ".join(counted_files[:8]) or "(no named files)"
        errors.append(
            f"diff against {base}: {total} changed lines exceed {limit}; "
            f"split the change. files: {preview}. "
            "update constraints.yaml size_limits.max_pr_diff_lines only with an explicit downgrade"
        )
        return report("diff", errors, "")
    return report(
        "diff",
        [],
        f"{total} changed lines against {base} (limit {limit}), all conform",
    )
