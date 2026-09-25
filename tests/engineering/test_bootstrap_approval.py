"""Bootstrap approval must not waive later or modified changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.engineering.check import main
from tests.engineering.conftest import git_init_and_commit


def _approve(root: Path, base: str) -> Path:
    source = root / "src/xagent/app.py"
    source.write_text("print('bootstrap')\n" * 500, encoding="utf-8")
    approval = root / ".engineering/bootstrap-approval.json"
    approval.parent.mkdir(exist_ok=True)
    approval.write_text(
        json.dumps(
            {
                "base": base,
                "scope": "user-approved initial engineering bootstrap only",
                "files": {
                    "src/xagent/app.py": hashlib.sha256(source.read_bytes()).hexdigest()
                },
            }
        ),
        encoding="utf-8",
    )
    return approval


def test_bootstrap_accepts_only_approved_content(git_repo: tuple[Path, str]) -> None:
    root, base = git_repo
    _approve(root, base)
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    source = root / "src/xagent/app.py"
    source.write_text(source.read_text() + "print('unapproved')\n")
    assert main(args) == 1


def test_bootstrap_rejects_extra_files(git_repo: tuple[Path, str]) -> None:
    root, base = git_repo
    _approve(root, base)
    (root / "extra.py").write_text("print('unapproved')\n")
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


@pytest.mark.parametrize("mutation", ["base", "scope", "malformed", "unknown-field"])
def test_bootstrap_rejects_invalid_approval(
    git_repo: tuple[Path, str], mutation: str
) -> None:
    root, base = git_repo
    approval = _approve(root, base)
    data = json.loads(approval.read_text())
    if mutation == "malformed":
        approval.write_text("[")
    else:
        data[mutation] = "unapproved"
        approval.write_text(json.dumps(data))
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


def test_bootstrap_cannot_be_reused_after_landing(git_repo: tuple[Path, str]) -> None:
    root, base = git_repo
    _approve(root, base)
    git_init_and_commit(root, "bootstrap")
    # Even rewriting approval to the new base cannot grant another exemption.
    from scripts.engineering.gitutil import resolve_reference

    landed = resolve_reference(root, "HEAD")
    _approve(root, landed)
    source = root / "src/xagent/app.py"
    source.write_text("print('next change')\n" * 500)
    approval = root / ".engineering/bootstrap-approval.json"
    data = json.loads(approval.read_text())
    data["files"]["src/xagent/app.py"] = hashlib.sha256(source.read_bytes()).hexdigest()
    approval.write_text(json.dumps(data))
    assert main(["--root", str(root), "--base", landed, "diff"]) == 1


@pytest.mark.parametrize("tracked", [False, True])
def test_bootstrap_binds_binary_file_membership(git_repo, tracked):
    from scripts.engineering.gitutil import run_git

    root, base = git_repo
    _approve(root, base)
    (root / "payload.bin").write_bytes(b"\x00\xff" * 32)
    if tracked:
        run_git(root, "add", "payload.bin")
    assert main(["--root", str(root), "--base", base, "diff"]) == 1
