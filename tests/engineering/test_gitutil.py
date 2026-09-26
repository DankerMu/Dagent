from __future__ import annotations

from pathlib import Path

import pytest

from scripts.engineering.gitutil import (
    diff_name_status,
    diff_numstat,
    list_scannable_files,
    list_source_at,
    porcelain_entries,
    resolve_reference,
    run_git,
    show_file,
    snapshot_tree,
)
from scripts.engineering.tools import ToolFailure
from tests.engineering.conftest import git_init_and_commit


def test_resolve_reference_returns_commit_sha(git_repo: tuple[Path, str]) -> None:
    root, sha = git_repo
    assert resolve_reference(root, "HEAD") == sha
    assert resolve_reference(root, sha) == sha


def test_resolve_reference_rejects_unknown_ref(git_repo: tuple[Path, str]) -> None:
    root, _sha = git_repo
    with pytest.raises(ToolFailure, match="failed"):
        resolve_reference(root, "definitely-missing-ref")


def test_diff_numstat_counts_text_and_tracks_binary_paths(
    git_repo: tuple[Path, str],
) -> None:
    root, sha = git_repo
    app = root / "src" / "xagent" / "app.py"
    app.write_text(
        app.read_text(encoding="utf-8") + "print('more')\n", encoding="utf-8"
    )
    (root / "src" / "xagent" / "blob.bin").write_bytes(b"\x00\x01\x02\x03" * 32)
    git_init_and_commit(root, "second")
    total, files = diff_numstat(root, sha)
    assert set(files) == {"src/xagent/app.py", "src/xagent/blob.bin"}
    assert total == 1


def test_diff_name_status_and_list_source_prefix_boundary(
    git_repo: tuple[Path, str],
) -> None:
    root, sha = git_repo
    extra = root / "src2" / "outside.py"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "xagent" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    git_init_and_commit(root, "prefixes")
    rows = diff_name_status(root, sha)
    paths = {path for _status, path in rows}
    assert "src/xagent/extra.py" in paths
    assert "src2/outside.py" in paths
    listed = list_source_at(root, "HEAD", ("src",))
    assert "src/xagent/extra.py" in listed
    assert "src2/outside.py" not in listed
    assert "src/xagent/app.py" in listed


def test_porcelain_entries_distinguish_modified_and_untracked_files(
    git_repo: tuple[Path, str],
) -> None:
    root, _sha = git_repo
    (root / "src" / "xagent" / "app.py").write_text(
        "def greet(name: str) -> str:\n    return name\n", encoding="utf-8"
    )
    (root / "untracked.py").write_text("print('new')\n", encoding="utf-8")
    entries = porcelain_entries(root)
    assert (" M", "src/xagent/app.py") in entries
    assert ("??", "untracked.py") in entries
    nested = root / "new-package" / "module.py"
    nested.parent.mkdir()
    nested.write_text("VALUE = 1\n")
    assert ("??", "new-package/module.py") in porcelain_entries(root)


def test_porcelain_entries_mark_untracked_separately(
    git_repo: tuple[Path, str],
) -> None:
    root, _sha = git_repo
    (root / "src" / "xagent" / "app.py").write_text(
        "def greet(name: str) -> str:\n    return name\n", encoding="utf-8"
    )
    (root / "untracked.py").write_text("print('new')\n", encoding="utf-8")
    rows = {path: status for status, path in porcelain_entries(root)}
    assert rows["untracked.py"].startswith("?")
    assert not rows["src/xagent/app.py"].startswith("?")


def test_show_file_returns_committed_contents_or_none(
    git_repo: tuple[Path, str],
) -> None:
    root, sha = git_repo
    text = show_file(root, sha, "src/xagent/app.py")
    assert text is not None
    assert "def greet" in text
    assert show_file(root, sha, "does/not/exist.py") is None


def test_list_scannable_files_honors_gitignore(git_repo: tuple[Path, str]) -> None:
    root, _sha = git_repo
    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (root / "ignored.py").write_text("SECRET = 1\n", encoding="utf-8")
    (root / "visible.py").write_text("VISIBLE = 1\n", encoding="utf-8")
    files = list_scannable_files(root)
    assert "visible.py" in files
    assert "src/xagent/app.py" in files
    assert "ignored.py" not in files


def test_list_scannable_files_walks_snapshot_without_git(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.js").write_text("generated\n", encoding="utf-8")
    files = list_scannable_files(tmp_path)
    assert "src/app.py" in files
    assert "build/out.js" not in files


def test_snapshot_tree_extracts_committed_tree_without_git_dir(
    git_repo: tuple[Path, str], tmp_path: Path
) -> None:
    root, sha = git_repo
    dest = tmp_path / "snapshot"
    snapshot_tree(root, sha, dest)
    assert (dest / "src" / "xagent" / "app.py").is_file()
    assert not (dest / ".git").exists()
    contents = (dest / "src" / "xagent" / "app.py").read_text(encoding="utf-8")
    assert "def greet" in contents


def test_run_git_reports_nonzero_as_tool_failure(git_repo: tuple[Path, str]) -> None:
    root, _sha = git_repo
    with pytest.raises(ToolFailure, match="git rev-parse --verify missing failed"):
        run_git(root, "rev-parse", "--verify", "missing")


def test_run_git_missing_binary_is_tool_failure(
    git_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _sha = git_repo
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ToolFailure, match="git is not installed"):
        run_git(root, "status")


def test_snapshot_tree_missing_ref_is_tool_failure(
    git_repo: tuple[Path, str], tmp_path: Path
) -> None:
    root, _sha = git_repo
    with pytest.raises(ToolFailure, match="git archive"):
        snapshot_tree(root, "missing-ref", tmp_path / "dest")


def test_list_scannable_files_skips_index_paths_missing_on_disk(
    git_repo: tuple[Path, str],
) -> None:
    root, _sha = git_repo
    tracked = root / "src" / "xagent" / "app.py"
    tracked.unlink()
    files = list_scannable_files(root)
    assert "src/xagent/app.py" not in files


def test_unstaged_rename_reports_destination(git_repo: tuple[Path, str]) -> None:
    root, _sha = git_repo
    src = root / "src" / "xagent" / "app.py"
    dest = root / "src" / "xagent" / "renamed.py"
    src.rename(dest)
    assert ("??", "src/xagent/renamed.py") in porcelain_entries(root)


def test_diff_helpers_ignore_blank_and_short_rows(
    git_repo: tuple[Path, str], monkeypatch
) -> None:
    root, sha = git_repo

    def fake_run(_root, *args: str) -> str:
        if args[:2] == ("diff", "--name-status"):
            return "\nM\tsrc/xagent/app.py\n"
        if args[:2] == ("diff", "--numstat"):
            return "not-enough\n1\t1\tsrc/xagent/app.py\n"
        if args[:1] == ("status",):
            return "\n M src/xagent/app.py\n"
        raise AssertionError(args)

    monkeypatch.setattr("scripts.engineering.gitutil.run_git", fake_run)
    rows = diff_name_status(root, sha)
    assert rows == [("M", "src/xagent/app.py")]
    total, files = diff_numstat(root, sha)
    assert files == ["src/xagent/app.py"]
    assert total == 2
    assert (" M", "src/xagent/app.py") in porcelain_entries(root)


def test_unstaged_reports_rename_destination_from_porcelain(
    git_repo: tuple[Path, str], monkeypatch
) -> None:
    root, _sha = git_repo
    monkeypatch.setattr(
        "scripts.engineering.gitutil.run_git",
        lambda root, *args: "R  src/xagent/app.py -> src/xagent/renamed.py\n",
    )
    assert porcelain_entries(root) == [("R ", "src/xagent/renamed.py")]


def test_resolve_reference_rejects_empty_sha(
    git_repo: tuple[Path, str], monkeypatch
) -> None:
    root, _sha = git_repo
    monkeypatch.setattr("scripts.engineering.gitutil.run_git", lambda root, *args: "\n")
    with pytest.raises(ToolFailure, match="did not resolve to a commit"):
        resolve_reference(root, "HEAD")


def test_snapshot_tree_tar_failure_is_tool_failure(
    git_repo: tuple[Path, str], tmp_path: Path, monkeypatch
) -> None:
    root, sha = git_repo
    calls = {"n": 0}

    def fake_run(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return type(
                "R",
                (),
                {"returncode": 0, "stdout": b"archive", "stderr": b""},
            )()
        return type(
            "R",
            (),
            {"returncode": 1, "stdout": b"", "stderr": b"tar exploded"},
        )()

    monkeypatch.setattr("scripts.engineering.gitutil.subprocess.run", fake_run)
    with pytest.raises(ToolFailure, match="tar extract"):
        snapshot_tree(root, sha, tmp_path / "dest")
