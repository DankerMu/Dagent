"""Stable source-span digests for detector finding identity."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .tools import ToolFailure


def _owned_text(root: Path, rel: str, cache: dict[str, str]) -> str:
    if rel in cache:
        return cache[rel]
    candidate = Path(rel)
    if not rel or candidate.is_absolute() or ".." in candidate.parts:
        raise ToolFailure(f"cannot read source for {rel}")
    path = (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ToolFailure(f"cannot read source for {rel}") from exc
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ToolFailure(f"cannot read source for {rel}") from exc
    cache[rel] = text
    return text


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    start = 0
    while True:
        index = text.find("\n", start)
        if index < 0:
            break
        start = index + 1
        if start < len(text):
            offsets.append(start)
    return offsets


def _line_range(text: str, line: int, *, allow_next: bool = False) -> tuple[int, int]:
    offsets = _line_offsets(text)
    if line < 1:
        raise ToolFailure("source coordinates are invalid")
    if line == len(offsets) + 1 and allow_next:
        return len(text), len(text)
    if line > len(offsets):
        raise ToolFailure("source coordinates are invalid")
    start = offsets[line - 1]
    end = offsets[line] if line < len(offsets) else len(text)
    if end > start and text[end - 1] == "\n":
        end -= 1
        if end > start and text[end - 1] == "\r":
            end -= 1
    return start, end


def _require_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolFailure(f"{label} is invalid")
    if value < 1:
        raise ToolFailure(f"{label} is invalid")
    return value


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def line_digest(root: Path, rel: str, line: int, cache: dict[str, str]) -> str:
    text = _owned_text(root, rel, cache)
    start, end = _line_range(text, line)
    return _digest(text[start:end])


def span_digest(
    root: Path,
    rel: str,
    start: dict[str, object],
    end: dict[str, object],
    cache: dict[str, str],
) -> str:
    start_line = _require_positive_int(start.get("line"), "start line")
    start_col = _require_positive_int(start.get("col"), "start col")
    end_line = _require_positive_int(end.get("line"), "end line")
    end_col = _require_positive_int(end.get("col"), "end col")
    if (end_line, end_col) < (start_line, start_col):
        raise ToolFailure("source coordinates are invalid")
    text = _owned_text(root, rel, cache)
    start_begin, start_limit = _line_range(text, start_line)
    end_begin, end_limit = _line_range(text, end_line, allow_next=end_col == 1)
    start_offset = start_begin + start_col - 1
    end_offset = end_begin + end_col - 1
    if start_offset < start_begin or start_offset > start_limit:
        raise ToolFailure("source coordinates are invalid")
    if end_offset < end_begin or end_offset > end_limit:
        raise ToolFailure("source coordinates are invalid")
    if end_offset < start_offset:
        raise ToolFailure("source coordinates are invalid")
    return _digest(text[start_offset:end_offset])


def next_occurrence(
    counts: dict[tuple[str, str], int], path: str, semantic: str
) -> str:
    key = (path, semantic)
    occurrence = counts.get(key, 0)
    counts[key] = occurrence + 1
    return f"{semantic}#{occurrence}"
