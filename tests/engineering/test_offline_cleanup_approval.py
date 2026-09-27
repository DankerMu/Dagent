"""Offline-cleanup approval must not waive later or modified changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.engineering.check import main
from tests.engineering.conftest import git_init_and_commit

OFFLINE_APPROVAL_PATH = ".engineering/offline-cleanup-approval.json"
OFFLINE_SCOPE = "user-approved isolated LAN cleanup only"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approve(root: Path, base: str, extra: dict[str, str | None] | None = None) -> Path:
    source = root / "src/xagent/app.py"
    source.write_text("print('offline-cleanup')\n" * 500, encoding="utf-8")
    files: dict[str, str | None] = {"src/xagent/app.py": _digest(source)}
    if extra:
        files.update(extra)
    approval = root / OFFLINE_APPROVAL_PATH
    approval.parent.mkdir(exist_ok=True)
    approval.write_text(
        json.dumps({"base": base, "scope": OFFLINE_SCOPE, "files": files}),
        encoding="utf-8",
    )
    return approval


def test_offline_cleanup_accepts_only_approved_content(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    _approve(root, base)
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    source = root / "src/xagent/app.py"
    source.write_text(source.read_text(encoding="utf-8") + "print('unapproved')\n")
    assert main(args) == 1


def test_offline_cleanup_keeps_subsequent_pr_limit(
    git_repo: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    root, base = git_repo
    _approve(root, base)
    assert main(["--root", str(root), "--base", base, "diff"]) == 0
    captured = capsys.readouterr()
    assert "exact user-approved isolated LAN cleanup snapshot" in captured.out
    assert "subsequent PR limit remains 400" in captured.out


def test_offline_cleanup_rejects_extra_files(git_repo: tuple[Path, str]) -> None:
    root, base = git_repo
    _approve(root, base)
    (root / "extra.py").write_text("print('unapproved')\n")
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


@pytest.mark.parametrize("mutation", ["base", "scope", "malformed", "unknown-field"])
def test_offline_cleanup_rejects_invalid_approval(
    git_repo: tuple[Path, str], mutation: str
) -> None:
    root, base = git_repo
    approval = _approve(root, base)
    data = json.loads(approval.read_text(encoding="utf-8"))
    if mutation == "malformed":
        approval.write_text("[")
    else:
        data[mutation] = "unapproved"
        approval.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


def test_offline_cleanup_cannot_be_reused_after_landing(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    _approve(root, base)
    git_init_and_commit(root, "offline-cleanup")
    from scripts.engineering.gitutil import resolve_reference

    landed = resolve_reference(root, "HEAD")
    _approve(root, landed)
    source = root / "src/xagent/app.py"
    source.write_text("print('next change')\n" * 500, encoding="utf-8")
    approval = root / OFFLINE_APPROVAL_PATH
    data = json.loads(approval.read_text(encoding="utf-8"))
    data["files"]["src/xagent/app.py"] = _digest(source)
    approval.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--root", str(root), "--base", landed, "diff"]) == 1


def test_offline_cleanup_rejects_base_replay_against_supplied_base(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    approval = _approve(root, base)
    data = json.loads(approval.read_text(encoding="utf-8"))
    data["base"] = (
        "ead53547a68bc07be0949be33f64dc16c49772ae"  # pragma: allowlist secret - Git commit ID
    )
    approval.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


@pytest.mark.parametrize("tracked", [False, True])
def test_offline_cleanup_binds_binary_file_membership(
    git_repo: tuple[Path, str], tracked: bool
) -> None:
    from scripts.engineering.gitutil import run_git

    root, base = git_repo
    _approve(root, base)
    payload = root / "payload.bin"
    payload.write_bytes(b"\x00\xff" * 32)
    if tracked:
        run_git(root, "add", "payload.bin")
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


def test_offline_cleanup_accepts_listed_binary_file(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    payload = root / "payload.bin"
    payload.write_bytes(b"\x00\xff" * 32)
    _approve(root, base, extra={"payload.bin": _digest(payload)})
    assert main(["--root", str(root), "--base", base, "diff"]) == 0


def test_offline_cleanup_untracked_symlink_cannot_escape_inventory(
    git_repo: tuple[Path, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    root, base = git_repo
    _approve(root, base)
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    target = tmp_path_factory.mktemp("outside-offline-inventory") / "payload.bin"
    target.write_bytes(b"escaped")
    (root / "extra.bin").symlink_to(target)
    assert main(args) == 1


def test_offline_cleanup_rejects_duplicate_json_keys_after_valid_snapshot(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    approval = _approve(root, base)
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    text = approval.read_text(encoding="utf-8")
    approval.write_text(text.replace('"files":', '"files": {}, "files":', 1))
    assert main(args) == 1


def test_offline_cleanup_rejects_noncanonical_digest_after_valid_snapshot(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    approval = _approve(root, base)
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    data = json.loads(approval.read_text(encoding="utf-8"))
    data["files"]["src/xagent/app.py"] = data["files"]["src/xagent/app.py"].upper()
    approval.write_text(json.dumps(data), encoding="utf-8")
    assert main(args) == 1


def test_offline_cleanup_rejects_source_under_symlinked_parent(
    git_repo: tuple[Path, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    root, base = git_repo
    # Keep the diff oversized after replacing the untracked source directory;
    # otherwise the ordinary <=400-line path does not consult an approval.
    retained = root / "retained.py"
    retained.write_text("print('retained')\n" * 500, encoding="utf-8")
    _approve(root, base, {"retained.py": _digest(retained)})
    args = ["--root", str(root), "--base", base, "diff"]
    assert main(args) == 0
    source = root / "src/xagent/app.py"
    target = tmp_path_factory.mktemp("outside-offline-source")
    (target / "app.py").write_bytes(source.read_bytes())
    source.unlink()
    source.parent.rmdir()
    source.parent.symlink_to(target, target_is_directory=True)
    assert main(args) == 1


def test_offline_cleanup_accepts_deleted_path_null(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    victim = root / "scripts/engineering/gate.py"
    victim.unlink()
    _approve(root, base, extra={"scripts/engineering/gate.py": None})
    assert main(["--root", str(root), "--base", base, "diff"]) == 0


def test_offline_cleanup_requires_deleted_paths_in_manifest(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    (root / "scripts/engineering/gate.py").unlink()
    _approve(root, base)
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


def test_offline_cleanup_rejects_digest_for_deleted_path(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    (root / "scripts/engineering/gate.py").unlink()
    _approve(
        root,
        base,
        extra={"scripts/engineering/gate.py": "0" * 64},
    )
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


@pytest.mark.parametrize("kind", ["extant", "symlink", "dangling-symlink"])
def test_offline_cleanup_null_cannot_hide_source(
    git_repo: tuple[Path, str], kind: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    root, base = git_repo
    victim = root / "scripts/engineering/gate.py"
    if kind == "extant":
        victim.write_text("still-present\n", encoding="utf-8")
    else:
        victim.unlink()
        if kind == "symlink":
            target = tmp_path_factory.mktemp("outside-cleanup") / "gate.py"
            target.write_text("escaped\n", encoding="utf-8")
            victim.symlink_to(target)
        else:
            victim.symlink_to("missing-offline-target")
    _approve(root, base, extra={"scripts/engineering/gate.py": None})
    assert main(["--root", str(root), "--base", base, "diff"]) == 1


def test_landed_bootstrap_does_not_block_offline_cleanup(
    git_repo: tuple[Path, str],
) -> None:
    root, base = git_repo
    leftover = root / ".engineering/bootstrap-approval.json"
    leftover.parent.mkdir(exist_ok=True)
    leftover.write_text(
        json.dumps(
            {
                "base": base,
                "scope": "user-approved initial engineering bootstrap only",
                "files": {"src/xagent/app.py": "0" * 64},
            }
        ),
        encoding="utf-8",
    )
    landed = git_init_and_commit(root, "land bootstrap approval")
    _approve(root, landed)
    assert main(["--root", str(root), "--base", landed, "diff"]) == 0
