"""RAGFlow's user-approved size exception is content-bound and single-use."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.engineering.check import main
from scripts.engineering.gitutil import resolve_reference
from tests.engineering.conftest import git_init_and_commit

APPROVAL = ".engineering/ragflow-approval.json"
SCOPE = "user-approved RAGFlow integration only"


def approve(root: Path, base: str) -> Path:
    source = root / "src/xagent/app.py"
    source.write_text("print('ragflow')\n" * 500)
    path = root / APPROVAL
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "base": base,
                "scope": SCOPE,
                "files": {
                    "src/xagent/app.py": hashlib.sha256(source.read_bytes()).hexdigest()
                },
            }
        )
    )
    return path


@pytest.mark.parametrize(
    "mutation", ["content", "extra", "base", "scope", "self", "duplicate"]
)
def test_ragflow_approval_rejects_drift(
    git_repo: tuple[Path, str], mutation: str
) -> None:
    root, base = git_repo
    path = approve(root, base)
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    data = json.loads(path.read_text())
    if mutation == "content":
        (root / "src/xagent/app.py").write_text("changed\n" * 500)
    elif mutation == "extra":
        (root / "extra.bin").write_bytes(b"\x00\xff")
    elif mutation == "duplicate":
        path.write_text(path.read_text().replace('"files":', '"files": {}, "files":'))
    else:
        if mutation == "self":
            data["files"][APPROVAL] = "0" * 64
        else:
            data[mutation] = "unapproved"
        path.write_text(json.dumps(data))
    assert main(args) == 1


def test_ragflow_approval_cannot_be_reused(git_repo: tuple[Path, str]) -> None:
    root, base = git_repo
    approve(root, base)
    assert main(["--root", str(root), "--base", base, "diff"]) == 0
    git_init_and_commit(root, "land-ragflow")
    landed = resolve_reference(root, "HEAD")
    path = approve(root, landed)
    source = root / "src/xagent/app.py"
    source.write_text("print('later')\n" * 500)
    data = json.loads(path.read_text())
    data["files"]["src/xagent/app.py"] = hashlib.sha256(source.read_bytes()).hexdigest()
    path.write_text(json.dumps(data))
    assert main(["--root", str(root), "--base", landed, "diff"]) == 1
