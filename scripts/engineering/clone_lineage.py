"""Prove unmatched jscpd occurrences against the immutable baseline source.

The detector's maximal fragments and first/second order are not historical IDs.
For each affected tracked path, non-autojunk equal whole-line blocks map
candidate lines to original lines; a displaced exact span can use a unique,
unoccupied original window. Each clone occurrence needs a contiguous origin.
When jscpd supplies columns, only its ASCII-covered start-line suffix and
end-line prefix must survive, anchored by contiguous unchanged interior lines.
Missing/malformed columns never authorize an edge edit. The reported fragment
is checked against firstFile only: token-equivalent second occurrences can
spell names differently. Original lines have one owner per candidate path;
overlapping detector reports may reuse the SAME candidate lines. Excess
covered windows are rejected even if a diff aligns a new copy ahead of its
survivor. Ambiguous moves, non-ASCII clipped edges, edits inside the covered
region, missing anchors, renamed/new paths and inaccessible source are never
presumed inherited. An exact replacement after deletion is indistinguishable
from a move if there is only one surviving copy. Frozen aggregate percentage
and count ceilings apply independently.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from .findings import Finding
from .gitutil import resolve_reference, run_git
from .tools import ToolFailure

Span = tuple[str, int, int, int | None, int | None]


def clone_span(file: dict[str, object]) -> tuple[int, int, int | None, int | None]:
    start_loc = file.get("startLoc")
    end_loc = file.get("endLoc")
    start = file.get("start") or (
        start_loc.get("line") if isinstance(start_loc, dict) else None
    )
    end = file.get("end") or (
        end_loc.get("line") if isinstance(end_loc, dict) else None
    )
    if type(start) is not int or type(end) is not int:
        return (0, 0, None, None)
    start_col = start_loc.get("column") if isinstance(start_loc, dict) else None
    end_col = end_loc.get("column") if isinstance(end_loc, dict) else None
    # A present but malformed column must not silently become whole-line proof.
    if start_col is not None and type(start_col) is not int:
        start_col = -1
    if end_col is not None and type(end_col) is not int:
        end_col = -1
    return start, end, start_col, end_col


def _source_lines(root: Path, revision: str, path: str) -> list[str] | None:
    if not path or Path(path).is_absolute() or ".." in Path(path).parts:
        return None
    try:
        names = run_git(
            root, "ls-tree", "-r", "--name-only", "-z", revision, "--", path
        )
        if path not in names.split("\0"):
            return None
        return run_git(root, "show", f"{revision}:{path}").splitlines(keepends=True)
    except UnicodeError as exc:
        raise ToolFailure(f"cannot decode frozen jscpd source {path!r}: {exc}") from exc


def _candidate_lines(root: Path, path: str) -> list[str]:
    file = root / path
    try:
        file.resolve().relative_to(root.resolve())
        return file.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ToolFailure(f"cannot read jscpd occurrence {path!r}: {exc}") from exc


def _valid(span: Span, length: int) -> bool:
    return (
        1 <= span[1] <= span[2] <= length
        and (span[3] is None or span[3] >= 0)
        and (span[4] is None or span[4] > 0)
    )


def _line_map(
    original: list[str], candidate: list[str], spans: set[Span]
) -> dict[int, int]:
    mapped: dict[int, int] = {}
    occupied: dict[int, int] = {}
    for old, new, size in SequenceMatcher(
        None, original, candidate, autojunk=False
    ).get_matching_blocks():
        for offset in range(size):
            mapped[new + offset] = old + offset
            occupied[old + offset] = new + offset

    # Longest first: a containing moved span can establish the provenance of
    # nested clone reports without charging the original lines a second time.
    for _, start, end, _, _ in sorted(
        spans, key=lambda span: (span[1] - span[2], span[1])
    ):
        left, right = start - 1, end
        if not 0 <= left < right <= len(candidate):
            continue
        if (
            all(
                mapped.get(line) == mapped.get(left, -1) + line - left
                for line in range(left, right)
            )
            and left in mapped
        ):
            continue
        fragment = candidate[left:right]
        candidates = [
            old
            for old in range(len(original) - len(fragment) + 1)
            if original[old : old + len(fragment)] == fragment
        ]
        if len(candidates) != 1:
            continue
        old = candidates[0]
        if any(
            mapped.get(line, old + line - left) != old + line - left
            or occupied.get(old + line - left, line) != line
            for line in range(left, right)
        ):
            continue
        for line in range(left, right):
            mapped[line] = old + line - left
            occupied[old + line - left] = line
    return mapped


def _source_mappings(
    root: Path, revision: str, all_spans: dict[str, set[Span]]
) -> dict[str, tuple[dict[int, int], list[str], list[str]]]:
    mappings: dict[str, tuple[dict[int, int], list[str], list[str]]] = {}
    for path, spans in all_spans.items():
        original = _source_lines(root, revision, path)
        if original is None:
            continue
        candidate = _candidate_lines(root, path)
        mappings[path] = (_line_map(original, candidate, spans), original, candidate)
    return mappings


def inherited_clones(
    root: Path, reference: str, unmatched: list[Finding], all_findings: list[Finding]
) -> set[Finding]:
    """Return only unmatched pairs with two proven, non-counterfeit origins."""
    revision = resolve_reference(root, reference)
    requested = {
        span[0] for finding in unmatched for span in finding.clone_occurrences or ()
    }
    all_spans: dict[str, set[Span]] = {path: set() for path in requested}
    for finding in all_findings:
        for span in finding.clone_occurrences or ():
            if span[0] in all_spans:
                all_spans[span[0]].add(span)
    mappings = _source_mappings(root, revision, all_spans)
    inherited: set[Finding] = set()
    for finding in unmatched:
        spans = finding.clone_occurrences or ()
        if len(spans) != 2 or not finding.clone_fragment:
            continue
        if _inherited_span(
            spans[0], finding.clone_fragment, mappings
        ) and _inherited_span(spans[1], None, mappings):
            inherited.add(finding)
    return inherited


def _inherited_span(
    span: Span,
    fragment: str | None,
    mappings: dict[str, tuple[dict[int, int], list[str], list[str]]],
) -> bool:
    evidence = mappings.get(span[0])
    if evidence is None or not _valid(span, len(evidence[2])):
        return False
    candidate = evidence[2][span[1] - 1 : span[2]]
    if fragment is not None and fragment not in "".join(candidate):
        return False
    old_start = _origin_start(span, evidence[0], len(evidence[1]))
    if old_start is None:
        return False
    if not _covered_lines_match(
        evidence[1][old_start : old_start + len(candidate)], candidate, span
    ):
        return False
    # A copied window can cause the diff to assign its survivor's origin to
    # the new copy. Compare multiplicities of only the detector-covered parts.
    return _occurrences(evidence[2], candidate, span) <= _occurrences(
        evidence[1], candidate, span
    )


def _origin_start(span: Span, mapped: dict[int, int], old_length: int) -> int | None:
    start, end = span[1] - 1, span[2]
    first = start + int(span[3] is not None and span[3] > 0)
    last = end - int(span[4] is not None)
    if first >= last:
        return None  # A clipped edge without a whole-line anchor is ambiguous.
    anchor = mapped.get(first)
    if anchor is None or any(
        mapped.get(line) != anchor + line - first for line in range(first, last)
    ):
        return None
    old = anchor - (first - start)
    if old < 0 or old + end - start > old_length:
        return None
    owners = {source: candidate for candidate, source in mapped.items()}
    if any(
        mapped.get(line, old + line - start) != old + line - start
        or owners.get(old + line - start, line) != line
        for line in range(start, end)
    ):
        return None
    return old


def _start_edge_matches(old: str, now: str, start_col: int) -> bool:
    return not (
        not old.isascii()
        or not now.isascii()
        or start_col >= len(now.rstrip("\r\n"))
        or not old.endswith(now[start_col:])
    )


def _end_edge_matches(old: str, now: str, end_col: int) -> bool:
    return not (
        not old.isascii()
        or not now.isascii()
        or not 0 < end_col <= len(now.rstrip("\r\n"))
        or not old.startswith(now[:end_col])
    )


def _covered_lines_match(source: list[str], candidate: list[str], span: Span) -> bool:
    if len(source) != len(candidate) or not source:
        return False
    start_col, end_col = span[3], span[4]
    for index, (old, now) in enumerate(zip(source, candidate)):
        if index == 0 and start_col is not None and start_col > 0:
            if not _start_edge_matches(old, now, start_col):
                return False
        elif index == len(candidate) - 1 and end_col is not None:
            if not _end_edge_matches(old, now, end_col):
                return False
        elif old != now:
            return False
    return True


def _occurrences(lines: list[str], candidate: list[str], span: Span) -> int:
    return sum(
        _covered_lines_match(lines[start : start + len(candidate)], candidate, span)
        for start in range(len(lines) - len(candidate) + 1)
    )
