"""The approved denominator-only adjustment never grants general baseline writes."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.engineering.coverage import _check_floor_authority
from scripts.engineering.diffcheck import (
    OFFLINE_CLEANUP_APPROVAL_PATH,
    OFFLINE_CLEANUP_SCOPE,
    offline_changed_paths,
)
from tests.engineering.conftest import git_init_and_commit

FLOORS = Path(".engineering/coverage-floors.json")


def _approved_bytes():
    candidate = (Path(__file__).resolve().parents[2] / FLOORS).read_bytes()
    assert (
        hashlib.sha256(candidate).hexdigest()
        == (
            "9b2715c76238f4c4ac44696e22167f073efd83c6f331c4d810cbfa4f5da4211f"  # pragma: allowlist secret - approved public artifact digest
        )
    )
    original = candidate.replace(
        b'"src/xagent/core/tools/adapters/vibe/base.py": 95.94594594594595',
        b'"src/xagent/core/tools/adapters/vibe/base.py": 96.0',
    )
    return original, candidate


def _seal(root, base):
    files = {}
    for name in offline_changed_paths(root, base) - {OFFLINE_CLEANUP_APPROVAL_PATH}:
        path = root / name
        files[name] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        )
    (root / OFFLINE_CLEANUP_APPROVAL_PATH).write_text(
        json.dumps({"base": base, "scope": OFFLINE_CLEANUP_SCOPE, "files": files})
    )


def _candidate(git_repo, *, artifact=FLOORS, different_old=False):
    root, _ = git_repo
    original, candidate = _approved_bytes()
    path = root / artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original + (b"\n" if different_old else b""))
    base = git_init_and_commit(root, "freeze coverage artifact")
    path.write_bytes(candidate)
    _seal(root, base)
    return root, base


def test_exact_authorized_floor_transition_accepts_only_sealed_snapshot(git_repo):
    root, base = _candidate(git_repo)
    assert _check_floor_authority(root, FLOORS, base) == []


@pytest.mark.parametrize(
    "tamper", ["missing_approval", "other_floor", "metadata", "source_drift"]
)
def test_floor_authority_rejects_outside_the_approved_transition(git_repo, tamper):
    root, base = _candidate(git_repo)
    if tamper == "missing_approval":
        (root / OFFLINE_CLEANUP_APPROVAL_PATH).unlink()
    elif tamper == "source_drift":
        (root / "src/xagent/unapproved.py").write_text("VALUE = 42\n")
    else:
        payload = json.loads((root / FLOORS).read_text())
        if tamper == "other_floor":
            payload["files"]["src/xagent/core/tools/adapters/vibe/basic_tools.py"] = 0
        else:
            payload["source_hash"] = "different source tree"
        (root / FLOORS).write_text(json.dumps(payload))
        _seal(root, base)
    assert _check_floor_authority(root, FLOORS, base)


def test_matching_candidate_cannot_replace_a_different_frozen_artifact(git_repo):
    root, base = _candidate(git_repo, different_old=True)
    assert _check_floor_authority(root, FLOORS, base)


def test_authorized_hashes_do_not_apply_to_an_alternate_artifact(git_repo):
    alternate = Path(".engineering/alternate-floors.json")
    root, base = _candidate(git_repo, artifact=alternate)
    assert _check_floor_authority(root, alternate, base)


def test_landed_approval_cannot_be_rearmed_for_the_same_floor_change(git_repo):
    root, _ = _candidate(git_repo)
    original, candidate = _approved_bytes()
    (root / FLOORS).write_bytes(original)
    landed = git_init_and_commit(root, "land consumed approval")
    (root / FLOORS).write_bytes(candidate)
    _seal(root, landed)
    assert _check_floor_authority(root, FLOORS, landed)
