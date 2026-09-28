"""PR diff-size gate with content-bound, single-base snapshot approvals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import ConfigError, load_constraints
from .gitutil import (
    diff_numstat,
    porcelain_entries,
    resolve_reference,
    run_git,
    show_file,
)
from .output import TOOL_FAILURE, report
from .tools import ToolFailure

BOOTSTRAP_APPROVAL_PATH = ".engineering/bootstrap-approval.json"
BOOTSTRAP_SCOPE = "user-approved initial engineering bootstrap only"
OFFLINE_CLEANUP_APPROVAL_PATH = ".engineering/offline-cleanup-approval.json"
OFFLINE_CLEANUP_SCOPE = "user-approved isolated LAN cleanup only"
RAGFLOW_APPROVAL_PATH = ".engineering/ragflow-approval.json"
RAGFLOW_SCOPE = "user-approved RAGFlow integration only"
_SHA256_HEX = 64


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != _SHA256_HEX:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate approval key: {key}")
        value[key] = item
    return value


def _approval_file_entries_valid(
    files: object, approval_path: str, allow_deletions: bool
) -> bool:
    if not isinstance(files, dict):
        return False
    for rel, digest in files.items():
        if not isinstance(rel, str) or not rel:
            return False
        path = Path(rel)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\0" in rel
            or rel == approval_path
        ):
            return False
        if digest is None:
            if not allow_deletions:
                return False
        elif not _is_sha256_digest(digest):
            return False
    return True


def _approval_metadata(
    path: Path, scope: str, allow_deletions: bool
) -> dict[str, Any] | None:
    try:
        approval = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(approval, dict) or set(approval) != {"base", "scope", "files"}:
        return None
    if approval["scope"] != scope or not isinstance(approval["base"], str):
        return None
    if len(approval["base"]) != 40 or any(
        char not in "0123456789abcdef" for char in approval["base"]
    ):
        return None
    approval_path = (
        RAGFLOW_APPROVAL_PATH
        if scope == RAGFLOW_SCOPE
        else OFFLINE_CLEANUP_APPROVAL_PATH
        if allow_deletions
        else BOOTSTRAP_APPROVAL_PATH
    )
    if not _approval_file_entries_valid(
        approval["files"], approval_path, allow_deletions
    ):
        return None
    return approval


def offline_approval_metadata_valid(path: Path) -> bool:
    """A landed approval remains public only while its digest schema is intact."""
    return _approval_metadata(path, OFFLINE_CLEANUP_SCOPE, True) is not None


def ragflow_approval_metadata_valid(path: Path) -> bool:
    """Validate the public digest schema for the one-time RAGFlow approval."""
    return _approval_metadata(path, RAGFLOW_SCOPE, True) is not None


def _path_escapes_root(root: Path, rel: str) -> bool:
    path = Path(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\0" in rel:
        return True
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            return True
    try:
        current.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return True
    return False


def offline_changed_paths(root: Path, base: str) -> set[str]:
    """Name every changed tracked and untracked path without text/binary guessing."""
    tracked = run_git(
        root,
        "-c",
        "core.quotepath=false",
        "diff",
        "--name-only",
        "--no-renames",
        "--no-ext-diff",
        "-z",
        base,
        "--",
    )
    untracked = run_git(
        root,
        "-c",
        "core.quotepath=false",
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
    )
    return {rel for rel in (tracked + untracked).split("\0") if rel}


def _snapshot_file_matches(
    root: Path, rel: str, recorded: object, allow_deletions: bool
) -> bool:
    if _path_escapes_root(root, rel):
        return False
    source = root / rel
    if recorded is None:
        return allow_deletions and not source.is_symlink() and not source.exists()
    if not _is_sha256_digest(recorded):
        return False
    if source.is_symlink() or not source.is_file():
        return False
    return recorded == hashlib.sha256(source.read_bytes()).hexdigest()


def approved_snapshot(
    root: Path,
    base: str,
    files: list[str],
    *,
    approval_path: str,
    scope: str,
    allow_deletions: bool,
) -> bool:
    """Accept only the exact approved snapshot before that approval lands."""
    path = root / approval_path
    if (
        path.is_symlink()
        or _path_escapes_root(root, approval_path)
        or not path.is_file()
    ):
        return False
    try:
        approval = _approval_metadata(path, scope, allow_deletions)
        if approval is None:
            return False
        if resolve_reference(root, base) != approval["base"]:
            return False
        # Once the approval has landed, no subsequent PR can reuse it.
        if show_file(root, base, approval_path) is not None:
            return False
        expected = approval["files"]
        actual_files = (
            offline_changed_paths(root, base) if allow_deletions else set(files)
        ) - {approval_path}
        if actual_files != set(expected):
            return False
        return all(
            _snapshot_file_matches(root, rel, expected[rel], allow_deletions)
            for rel in actual_files
        )
    except (OSError, ValueError, TypeError, ToolFailure):
        return False


def approved_bootstrap(root: Path, base: str, files: list[str]) -> bool:
    """Accept only the exact approved snapshot before the first bootstrap merge."""
    return approved_snapshot(
        root,
        base,
        files,
        approval_path=BOOTSTRAP_APPROVAL_PATH,
        scope=BOOTSTRAP_SCOPE,
        allow_deletions=False,
    )


def approved_offline_cleanup(root: Path, base: str) -> bool:
    """Accept only the exact approved isolated-LAN cleanup snapshot."""
    return approved_snapshot(
        root,
        base,
        [],
        approval_path=OFFLINE_CLEANUP_APPROVAL_PATH,
        scope=OFFLINE_CLEANUP_SCOPE,
        allow_deletions=True,
    )


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
        if approved_offline_cleanup(root, base):
            return report(
                "diff",
                [],
                f"{total} changed lines: exact user-approved isolated LAN cleanup snapshot; "
                f"subsequent PR limit remains {limit}",
            )
        if approved_snapshot(
            root,
            base,
            [],
            approval_path=RAGFLOW_APPROVAL_PATH,
            scope=RAGFLOW_SCOPE,
            allow_deletions=True,
        ):
            return report(
                "diff",
                [],
                f"{total} changed lines: exact user-approved RAGFlow snapshot; "
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
