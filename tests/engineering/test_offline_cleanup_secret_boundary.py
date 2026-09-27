"""Only verifiable offline-cleanup digest data can avoid credential false positives."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from scripts.engineering.check import main
from scripts.engineering.findings import make_finding
from scripts.engineering.security import collect_security_findings
from tests.engineering import test_approval_secret_boundary as bootstrap_fixtures
from tests.engineering.conftest import git_init_and_commit
from tests.engineering.test_offline_cleanup_approval import (
    OFFLINE_APPROVAL_PATH,
    _approve,
)
from tests.engineering.test_security_cli import _write_security_baseline

bootstrap_approval_repo = bootstrap_fixtures.approval_repo


@pytest.fixture
def approval_repo(git_repo, monkeypatch):
    root, _ = git_repo
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    )
    _write_security_baseline(root, collect_security_findings(root, secrets_only=True))
    base = git_init_and_commit(root, "freeze fixture scanner findings")
    return root, base, _approve(root, base)


def _check(root, base):
    return main(["--root", str(root), "--base", base, "secrets"])


def test_verified_digests_do_not_hide_other_json_findings(approval_repo, capsys):
    root, base, approval = approval_repo
    assert _check(root, base) == 0
    canary = root / "canary.json"
    canary.write_text(
        json.dumps({"value": hashlib.sha256(b"unannotated canary").hexdigest()})
    )
    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload["files"]["canary.json"] = hashlib.sha256(canary.read_bytes()).hexdigest()
    approval.write_text(json.dumps(payload), encoding="utf-8")

    assert _check(root, base) == 1
    assert "canary.json" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", ["metadata", "hash", "source"])
def test_unverified_approval_is_still_scanned(approval_repo, mutation):
    root, base, approval = approval_repo
    payload = json.loads(approval.read_text(encoding="utf-8"))
    if mutation == "metadata":
        payload["extra"] = hashlib.sha256(b"unapproved metadata").hexdigest()
    elif mutation == "hash":
        payload["files"]["src/xagent/app.py"] = "0" * 64
    else:
        (root / "src/xagent/app.py").write_text("print('changed')\n")
    approval.write_text(json.dumps(payload), encoding="utf-8")
    assert _check(root, base) == 1


def test_committed_approval_must_remain_byte_identical(approval_repo):
    root, base, approval = approval_repo
    assert _check(root, base) == 0
    landed = git_init_and_commit(root, "land verified approval")
    (root / "src/xagent/app.py").write_text("print('ordinary later change')\n")
    assert _check(root, landed) == 0

    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload["extra"] = hashlib.sha256(b"candidate-only metadata").hexdigest()
    approval.write_text(json.dumps(payload), encoding="utf-8")
    assert _check(root, landed) == 1


def test_committed_malformed_metadata_is_not_public(
    approval_repo,
) -> None:
    root, _, approval = approval_repo
    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload["extra"] = hashlib.sha256(b"unapproved committed metadata").hexdigest()
    approval.write_text(json.dumps(payload), encoding="utf-8")
    landed = git_init_and_commit(root, "land malformed approval")
    (root / "src/xagent/app.py").write_text("print('ordinary later change')\n")
    assert _check(root, landed) == 1


def test_approval_requires_an_explicit_authority(approval_repo):
    root, _, _ = approval_repo
    assert main(["--root", str(root), "secrets"]) == 1


def test_symlinked_approval_is_not_trusted(approval_repo, tmp_path_factory):
    root, base, approval = approval_repo
    target = tmp_path_factory.mktemp("outside-offline-approval") / "approval.json"
    target.write_bytes(approval.read_bytes())
    approval.unlink()
    approval.symlink_to(target)
    assert _check(root, base) == 2


def test_other_detectors_on_approval_are_kept(approval_repo, monkeypatch, capsys):
    root, base, approval = approval_repo
    payload = json.loads(approval.read_text(encoding="utf-8"))
    digest = next(value for value in payload["files"].values() if value is not None)
    hashed_digest = hashlib.sha1(digest.encode("utf-8")).hexdigest()
    hex_finding = make_finding(
        check="secret",
        path=OFFLINE_APPROVAL_PATH,
        line=1,
        detector="detect-secrets",
        severity="error",
        identity=f"Hex High Entropy String:{hashed_digest}",
        detail=f"Hex High Entropy String at {OFFLINE_APPROVAL_PATH}:1",
        include_line=False,
    )
    keyword = make_finding(
        check="secret",
        path=OFFLINE_APPROVAL_PATH,
        line=2,
        detector="detect-secrets",
        severity="error",
        identity="Secret Keyword:abc",
        detail=f"Secret Keyword at {OFFLINE_APPROVAL_PATH}:2",
    )
    monkeypatch.setattr(
        "scripts.engineering.security.collect_security_findings",
        lambda root, secrets_only=False: [hex_finding, keyword],
    )
    assert _check(root, base) == 1
    err = capsys.readouterr().err
    assert "Secret Keyword" in err
    assert "Hex High Entropy String" not in err


@pytest.mark.parametrize(
    "approval_fixture", ["approval_repo", "bootstrap_approval_repo"]
)
@pytest.mark.parametrize("landed", [False, True])
def test_hex_filename_is_not_exempt_as_a_manifest_digest(
    request, approval_fixture, landed
):
    root, base, approval = request.getfixturevalue(approval_fixture)
    assert _check(root, base) == 0
    filename = hashlib.sha256(
        b"credential-shaped-filename-not-a-content-digest"
    ).hexdigest()
    source = root / filename
    source.write_text("ordinary file contents\n", encoding="utf-8")
    payload = json.loads(approval.read_text(encoding="utf-8"))
    payload["files"][filename] = hashlib.sha256(source.read_bytes()).hexdigest()
    approval.write_text(json.dumps(payload), encoding="utf-8")
    if landed:
        base = git_init_and_commit(
            root, "land manifest with credential-shaped filename"
        )
    assert _check(root, base) == 1
