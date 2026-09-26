from __future__ import annotations

from pathlib import Path

from scripts.engineering.check import main
from tests.engineering.conftest import git_init_and_commit, write_constraints


def test_diff_accepts_small_change(clean_repo: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.engineering.diffcheck.diff_numstat",
        lambda root, base: (12, ["src/xagent/app.py"]),
    )
    monkeypatch.setattr(
        "scripts.engineering.diffcheck.porcelain_entries",
        lambda root: [],
    )
    code = main(["--root", str(clean_repo), "--base", "dagent", "diff"])
    captured = capsys.readouterr()
    assert code == 0
    assert "12 changed lines" in captured.out


def test_diff_rejects_over_limit(clean_repo: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.engineering.diffcheck.diff_numstat",
        lambda root, base: (401, ["src/xagent/app.py"]),
    )
    monkeypatch.setattr(
        "scripts.engineering.diffcheck.porcelain_entries",
        lambda root: [],
    )
    code = main(["--root", str(clean_repo), "--base", "dagent", "diff"])
    captured = capsys.readouterr()
    assert code == 1
    assert "401 changed lines exceed 400" in captured.err


def test_diff_untracked_file_counts_against_limit(
    git_repo: tuple[Path, str], capsys
) -> None:
    root, sha = git_repo
    write_constraints(root, **{"size_limits.max_pr_diff_lines.value": 5})
    (root / "untracked.py").write_text("a\nb\nc\nd\ne\nf\n", encoding="utf-8")
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 1
    assert "changed lines exceed 5" in captured.err
    assert "untracked.py" in captured.err


def test_diff_accepts_committed_small_change(
    git_repo: tuple[Path, str], capsys
) -> None:
    root, sha = git_repo
    app = root / "src" / "xagent" / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + "x = 1\n", encoding="utf-8")
    git_init_and_commit(root, "tiny")
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 0
    assert "changed lines against" in captured.out
    assert "all conform" in captured.out


def test_diff_unstaged_edit_counts_changed_lines_not_whole_file(
    git_repo: tuple[Path, str], capsys
) -> None:
    root, sha = git_repo
    app = root / "src" / "xagent" / "app.py"
    original = app.read_text(encoding="utf-8")
    app.write_text(original + "print('one')\nprint('two')\n", encoding="utf-8")
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 0
    assert "2 changed lines against" in captured.out
    assert "all conform" in captured.out


def test_diff_skips_untracked_directory_entries(
    git_repo: tuple[Path, str], capsys
) -> None:
    root, sha = git_repo
    (root / "scratchdir").mkdir()
    (root / "scratchdir" / "note.txt").write_text("hi\n", encoding="utf-8")
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 0
    assert "1 changed lines against" in captured.out
    assert "all conform" in captured.out


def test_diff_skips_unreadable_untracked_without_crash(
    git_repo: tuple[Path, str], capsys
) -> None:
    root, sha = git_repo
    binary = root / "payload.bin"
    binary.write_bytes(b"\x00\xff" * 40)
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 0
    assert "all conform" in captured.out


def test_diff_missing_git_is_tool_failure(
    git_repo: tuple[Path, str], capsys, monkeypatch
) -> None:
    root, sha = git_repo
    monkeypatch.setenv("PATH", "")
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 2
    assert "diff:" in captured.err
    assert "git is not installed" in captured.err


def test_diff_malformed_constraints_is_tool_failure(
    git_repo: tuple[Path, str], capsys
) -> None:
    root, sha = git_repo
    (root / "constraints.yaml").write_text("[]\n", encoding="utf-8")
    code = main(["--root", str(root), "--base", sha, "diff"])
    captured = capsys.readouterr()
    assert code == 2
    assert "must be a mapping" in captured.err
