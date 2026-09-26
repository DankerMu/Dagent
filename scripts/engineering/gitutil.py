"""Git helpers for diff and baseline capture."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .tools import ToolFailure


def run_git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolFailure("git is not installed") from exc
    if completed.returncode != 0:
        detail = (
            completed.stderr or completed.stdout
        ).strip() or f"exit {completed.returncode}"
        raise ToolFailure(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def resolve_reference(root: Path, reference: str) -> str:
    sha = run_git(root, "rev-parse", "--verify", f"{reference}^{{commit}}").strip()
    if not sha:
        raise ToolFailure(f"git reference {reference!r} did not resolve to a commit")
    return sha


def diff_name_status(root: Path, base: str) -> list[tuple[str, str]]:
    output = run_git(root, "diff", "--name-status", "--find-renames", base, "--")
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        rows.append((status, path))
    return rows


def porcelain_entries(root: Path) -> list[tuple[str, str]]:
    output = run_git(root, "status", "--porcelain", "--untracked-files=all")
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((status, path))
    return rows


def diff_numstat(root: Path, base: str) -> tuple[int, list[str]]:
    output = run_git(root, "diff", "--numstat", base, "--")
    added = 0
    removed = 0
    files: list[str] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        plus, minus, path = parts[0], parts[1], parts[2]
        files.append(path)
        if plus == "-" or minus == "-":
            continue
        added += int(plus)
        removed += int(minus)
    return added + removed, files


def show_file(root: Path, reference: str, relpath: str) -> str | None:
    completed = subprocess.run(
        ["git", "show", f"{reference}:{relpath}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def list_source_at(root: Path, reference: str, prefixes: tuple[str, ...]) -> set[str]:
    output = run_git(root, "ls-tree", "-r", "--name-only", reference)
    files: set[str] = set()
    for line in output.splitlines():
        rel = line.strip()
        if any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in prefixes):
            files.add(rel)
    return files


def list_scannable_files(root: Path) -> list[str]:
    """Git-tracked plus non-ignored untracked files; walk a snapshot without .git."""
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        from .config import iter_files, relative_to_root

        return sorted(relative_to_root(root, path) for path in iter_files(root))
    output = run_git(
        root,
        "-c",
        "core.quotepath=false",
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    files: list[str] = []
    for rel in output.split("\0"):
        if not rel:
            continue
        path = root / rel
        if path.is_file():
            files.append(rel)
    return files


def snapshot_tree(root: Path, reference: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", reference],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        detail = (archive.stderr or b"").decode("utf-8", "replace").strip()
        raise ToolFailure(f"git archive {reference} failed: {detail}")
    extracted = subprocess.run(
        ["tar", "-xf", "-"],
        cwd=dest,
        input=archive.stdout,
        capture_output=True,
        check=False,
    )
    if extracted.returncode != 0:
        detail = (extracted.stderr or b"").decode("utf-8", "replace").strip()
        raise ToolFailure(f"tar extract of {reference} failed: {detail}")
