from __future__ import annotations

from pathlib import Path

from scripts.engineering.check import main
from tests.engineering.conftest import write_constraints


def test_docs_accepts_clean_fixture(clean_repo: Path, capsys) -> None:
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 0
    assert "docs:" in captured.out
    assert "conform" in captured.out


def test_docs_rejects_command_without_makefile_target(clean_repo: Path, capsys) -> None:
    makefile = clean_repo / "Makefile"
    text = "\n".join(
        line
        for line in makefile.read_text(encoding="utf-8").splitlines()
        if "smoke" not in line
    )
    makefile.write_text(text + "\n", encoding="utf-8")
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 1
    assert "make smoke" in captured.err
    assert "no Makefile target" in captured.err


def test_docs_rejects_unmirrored_matrix_command(clean_repo: Path, capsys) -> None:
    agents = clean_repo / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    agents.write_text(
        text.replace(
            "| Real database behavior | `make test-integration` | db |",
            "| Real database behavior | `make test-integration` | db |\n"
            "| Extra | `make lint` | extra |",
        ),
        encoding="utf-8",
    )
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 1
    assert "not mirrored" in captured.err


def test_docs_rejects_root_line_budget_overflow(clean_repo: Path, capsys) -> None:
    write_constraints(clean_repo, **{"documentation.agents_md_max_lines": 20})
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 1
    assert "expected at most 20 lines" in captured.err


def test_docs_rejects_glossary_defined_in_context(clean_repo: Path, capsys) -> None:
    context = clean_repo / "CONTEXT.md"
    context.write_text(
        context.read_text(encoding="utf-8").replace(
            "Confirmed definitions live only in `openspec/glossary.md`.",
            "**Agent**:\nA second definition.\n",
        ),
        encoding="utf-8",
    )
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 1
    assert "defines terms" in captured.err


def test_docs_rejects_missing_known_limitations(clean_repo: Path, capsys) -> None:
    agents = clean_repo / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            "## Known Limitations and Deferred Work\n\nFixture limitations.\n",
            "",
        ),
        encoding="utf-8",
    )
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Known Limitations and Deferred Work" in captured.err


def test_docs_rejects_stale_exemption_target(clean_repo: Path, capsys) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/missing.py",
                    "rules": ["size_limits"],
                    "reason": "generated",
                    "exit_condition": "split files",
                }
            ]
        },
    )
    code = main(["--root", str(clean_repo), "docs"])
    captured = capsys.readouterr()
    assert code == 1
    assert "renamed or removed" in captured.err
