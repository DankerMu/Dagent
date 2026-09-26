"""Only verifiable bootstrap digest data can avoid credential false positives."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from scripts.engineering.check import main
from scripts.engineering.security import collect_security_findings
from tests.engineering.conftest import git_init_and_commit
from tests.engineering.test_bootstrap_approval import _approve
from tests.engineering.test_security_cli import _write_security_baseline


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
    payload = json.loads(approval.read_text())
    payload["files"]["canary.json"] = hashlib.sha256(canary.read_bytes()).hexdigest()
    approval.write_text(json.dumps(payload))

    assert _check(root, base) == 1
    assert "canary.json" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", ["metadata", "hash", "source"])
def test_unverified_approval_is_still_scanned(approval_repo, mutation):
    root, base, approval = approval_repo
    payload = json.loads(approval.read_text())
    if mutation == "metadata":
        payload["extra"] = hashlib.sha256(b"unapproved metadata").hexdigest()
    elif mutation == "hash":
        payload["files"]["src/xagent/app.py"] = "0" * 64
    else:
        (root / "src/xagent/app.py").write_text("print('changed')\n")
    approval.write_text(json.dumps(payload))
    assert _check(root, base) == 1


def test_committed_approval_must_remain_byte_identical(approval_repo):
    root, base, approval = approval_repo
    assert _check(root, base) == 0
    landed = git_init_and_commit(root, "land verified approval")
    (root / "src/xagent/app.py").write_text("print('ordinary later change')\n")
    assert _check(root, landed) == 0

    payload = json.loads(approval.read_text())
    payload["extra"] = hashlib.sha256(b"candidate-only metadata").hexdigest()
    approval.write_text(json.dumps(payload))
    assert _check(root, landed) == 1


def test_approval_requires_an_explicit_authority(approval_repo):
    root, _, _ = approval_repo
    assert main(["--root", str(root), "secrets"]) == 1


def test_symlinked_approval_is_not_trusted(approval_repo, tmp_path_factory):
    root, base, approval = approval_repo
    target = tmp_path_factory.mktemp("outside-approval") / "approval.json"
    target.write_bytes(approval.read_bytes())
    approval.unlink()
    approval.symlink_to(target)
    assert _check(root, base) == 2
