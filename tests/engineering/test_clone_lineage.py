"""Historical clone identity is per occurrence, not per detector fragment."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.engineering.adapters import parse_jscpd_payload
from scripts.engineering.config import load_constraints
from scripts.engineering.findings import compare_findings, serialize_baseline
from scripts.engineering.tools import ToolFailure
from tests.engineering.conftest import git_init_and_commit

BLOCK = "alpha\nbeta\ngamma\ndelta\nepsilon\n"
OLD_FRAGMENT = "beta\ngamma\ndelta\n"
A = "src/clone_a.py"
B = "src/clone_b.py"
C = "src/clone_c.py"
D = "src/clone_d.py"


def _put(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _pair(
    left: str, right: str, start_left: int, start_right: int, fragment: str, lines: int
) -> dict[str, object]:
    return {
        "firstFile": {"name": left, "start": start_left, "end": start_left + lines - 1},
        "secondFile": {
            "name": right,
            "start": start_right,
            "end": start_right + lines - 1,
        },
        "fragment": fragment,
    }


def _findings(root: Path, *pairs: dict[str, object], rate: float = 0):
    return parse_jscpd_payload(
        root,
        load_constraints(root),
        {"statistics": {"total": {"percentage": rate}}, "duplicates": list(pairs)},
    )


def _history(root: Path, *, second_pair: bool = False):
    _put(root, A, "a header\n" + BLOCK + "a footer\n")
    _put(root, B, "b header\n" + BLOCK + "b footer\n")
    pairs = [_pair(A, B, 3, 3, OLD_FRAGMENT, 3)]
    if second_pair:
        _put(root, C, "c header\n" + BLOCK + "c footer\n")
        _put(root, D, "d header\n" + BLOCK + "d footer\n")
        pairs.append(_pair(C, D, 3, 3, OLD_FRAGMENT, 3))
    sha = git_init_and_commit(root)
    baseline = serialize_baseline(
        reference=sha, tool_identities={}, findings=_findings(root, *pairs)
    )
    return sha, baseline


def _errors(
    root: Path, sha: str, baseline: dict, *pairs: dict[str, object], rate: float = 0
) -> list[str]:
    return compare_findings(
        _findings(root, *pairs, rate=rate), baseline, root=root, reference=sha
    )


def test_pair_order_keeps_frozen_identity(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    assert _errors(clean_repo, sha, baseline, _pair(B, A, 3, 3, OLD_FRAGMENT, 3)) == []


def test_maximal_fragment_boundary_shifts_without_new_source(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    assert _errors(clean_repo, sha, baseline, _pair(B, A, 2, 2, BLOCK, 5)) == []


def test_token_equivalent_method_names_have_independent_origins(
    clean_repo: Path,
) -> None:
    chat = "    async def chat(self):\n"
    stream = "    async def stream_chat(self):\n"
    _put(clean_repo, A, "a header\n" + chat + BLOCK)
    _put(clean_repo, B, "b header\n" + stream + BLOCK)
    sha = git_init_and_commit(clean_repo)
    baseline = serialize_baseline(
        reference=sha,
        tool_identities={},
        findings=_findings(clean_repo, _pair(A, B, 4, 4, OLD_FRAGMENT, 3)),
    )
    assert _errors(clean_repo, sha, baseline, _pair(A, B, 2, 2, chat + BLOCK, 6)) == []


def test_deleted_prefix_and_moved_originals_keep_lineage(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    _put(clean_repo, A, "a header\na footer\n" + BLOCK)
    _put(clean_repo, B, BLOCK + "b footer\n")
    assert _errors(clean_repo, sha, baseline, _pair(A, B, 3, 1, BLOCK, 5)) == []


def test_added_third_copy_cannot_spend_surviving_origin(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo, second_pair=True)
    _put(clean_repo, A, "a header\n" + BLOCK + BLOCK + "a footer\n")
    _put(clean_repo, C, "c header\nc footer\n")
    _put(clean_repo, D, "d header\nd footer\n")
    # One old pair was deleted, so the aggregate count is still below its ceiling.
    errors = _errors(clean_repo, sha, baseline, _pair(A, B, 7, 2, BLOCK, 5))
    assert any("new duplicate_code finding" in error for error in errors)


def test_known_clone_fingerprint_has_frozen_occurrence_capacity(
    clean_repo: Path,
) -> None:
    sha, baseline = _history(clean_repo, second_pair=True)
    _put(clean_repo, A, "a header\n" + BLOCK + BLOCK + "a footer\n")
    _put(clean_repo, C, "c header\nc footer\n")
    _put(clean_repo, D, "d header\nd footer\n")
    # The new A occurrence has the very same pair/fragment fingerprint as the
    # survivor. Removing C/D leaves the aggregate under its frozen ceiling.
    errors = _errors(
        clean_repo,
        sha,
        baseline,
        _pair(B, A, 3, 3, OLD_FRAGMENT, 3),
        _pair(A, B, 8, 3, OLD_FRAGMENT, 3),
    )
    assert any("new duplicate_code finding" in error for error in errors)
    assert not any("duplicate_code count rose" in error for error in errors)


def test_expanding_duplication_into_new_lines_is_debt(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    _put(clean_repo, A, "a header\n" + BLOCK + "zeta\na footer\n")
    _put(clean_repo, B, "b header\n" + BLOCK + "zeta\nb footer\n")
    errors = _errors(clean_repo, sha, baseline, _pair(A, B, 2, 2, BLOCK + "zeta\n", 6))
    assert any("new duplicate_code finding" in error for error in errors)


def test_deleting_legacy_pair_does_not_exempt_unrelated_copy(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    _put(clean_repo, A, "a header\na footer\n")
    _put(clean_repo, B, "b header\nb footer\n")
    _put(clean_repo, "src/fresh_a.py", BLOCK)
    _put(clean_repo, "src/fresh_b.py", BLOCK)
    errors = _errors(
        clean_repo,
        sha,
        baseline,
        _pair("src/fresh_a.py", "src/fresh_b.py", 1, 1, BLOCK, 5),
    )
    assert any("new duplicate_code finding" in error for error in errors)


def test_missing_immutable_source_fails_closed(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    with pytest.raises(ToolFailure, match="git rev-parse"):
        _errors(clean_repo, "missing-reference", baseline, _pair(A, B, 2, 2, BLOCK, 5))


def test_aggregate_rate_and_count_still_block(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    baseline = serialize_baseline(
        reference=sha,
        tool_identities={},
        findings=_findings(clean_repo, _pair(A, B, 3, 3, OLD_FRAGMENT, 3), rate=5),
    )
    errors = _errors(
        clean_repo,
        sha,
        baseline,
        _pair(A, B, 2, 2, BLOCK, 5),
        _pair(B, A, 2, 2, BLOCK, 5),
        rate=9,
    )
    assert any("duplicate_code count rose" in error for error in errors)
    assert any("worsened duplicate_code" in error for error in errors)


def test_one_historical_occurrence_does_not_authorize_a_pair(clean_repo: Path) -> None:
    _put(clean_repo, A, "a header\n" + BLOCK)
    _put(clean_repo, B, "b header\nno original clone\n")
    _put(clean_repo, C, "c header\n" + BLOCK)
    sha = git_init_and_commit(clean_repo)
    baseline = serialize_baseline(
        reference=sha,
        tool_identities={},
        findings=_findings(clean_repo, _pair(A, C, 3, 3, OLD_FRAGMENT, 3)),
    )
    _put(clean_repo, B, "b header\n" + BLOCK)
    errors = _errors(clean_repo, sha, baseline, _pair(A, B, 2, 2, BLOCK, 5))
    assert any("new duplicate_code finding" in error for error in errors)


def test_missing_detector_end_cannot_prove_lineage(clean_repo: Path) -> None:
    sha, baseline = _history(clean_repo)
    clone = _pair(A, B, 2, 2, BLOCK, 5)
    clone["secondFile"].pop("end")
    errors = _errors(clean_repo, sha, baseline, clone)
    assert any("new duplicate_code finding" in error for error in errors)


CLIPPED_MIDDLE = "    base_url: str,\n    api_key: str,\n"
CLIPPED = "    model: str,\n" + CLIPPED_MIDDLE + "    timeout: int = 10\n"


def _clipped_history(root: Path):
    _put(
        root, A, "    model: str = CLOUD,\n" + CLIPPED_MIDDLE + "    timeout: int = 5\n"
    )
    _put(
        root, B, "    model: str = LOCAL,\n" + CLIPPED_MIDDLE + "    timeout: int = 6\n"
    )
    sha = git_init_and_commit(root)
    baseline = serialize_baseline(
        reference=sha,
        tool_identities={},
        findings=_findings(root, _pair(A, B, 2, 2, CLIPPED_MIDDLE, 2)),
    )
    _put(root, A, CLIPPED)
    _put(root, B, CLIPPED)
    return sha, baseline


def _clipped_pair(left_start: int = 1, fragment: str = CLIPPED):
    pair = _pair(A, B, left_start, 1, fragment, 4)
    for file in (pair["firstFile"], pair["secondFile"]):
        file["startLoc"] = {"column": len("    model: str")}
        file["endLoc"] = {"column": len("    timeout: int")}
    return pair


def test_edits_outside_clipped_clone_edges_preserve_lineage(clean_repo: Path) -> None:
    sha, baseline = _clipped_history(clean_repo)
    assert _errors(clean_repo, sha, baseline, _clipped_pair()) == []


def test_edits_inside_clipped_clone_are_new_debt(clean_repo: Path) -> None:
    sha, baseline = _clipped_history(clean_repo)
    edited = CLIPPED.replace("    model: str,", "    model: str;")
    _put(clean_repo, A, edited)
    _put(clean_repo, B, edited)
    errors = _errors(clean_repo, sha, baseline, _clipped_pair(fragment=edited))
    assert any("new duplicate_code finding" in error for error in errors)


def test_added_clipped_copy_cannot_reuse_edge_origins(clean_repo: Path) -> None:
    sha, baseline = _clipped_history(clean_repo)
    _put(clean_repo, A, CLIPPED + CLIPPED)
    errors = _errors(clean_repo, sha, baseline, _clipped_pair(left_start=5))
    assert any("new duplicate_code finding" in error for error in errors)
