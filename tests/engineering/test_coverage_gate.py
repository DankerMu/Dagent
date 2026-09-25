from __future__ import annotations

import json
from pathlib import Path

from scripts.engineering.check import main
from scripts.engineering.gitutil import run_git
from tests.engineering.conftest import git_init_and_commit, write_coverage_reports


def test_coverage_accepts_complete_inventory(clean_repo: Path, capsys) -> None:
    pytest_json, vitest = write_coverage_reports(clean_repo)
    code = main(
        [
            "--root",
            str(clean_repo),
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "inventoried" in captured.out


def test_coverage_rejects_missing_source_file(clean_repo: Path, capsys) -> None:
    extra = clean_repo / "src" / "xagent" / "extra.py"
    extra.write_text("X = 1\n", encoding="utf-8")
    pytest_json, vitest = write_coverage_reports(clean_repo)
    code = main(
        [
            "--root",
            str(clean_repo),
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "src/xagent/extra.py" in captured.err
    assert "missing from pytest coverage JSON" in captured.err


def test_coverage_rejects_new_file_below_floor(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.coverage._historical_paths",
        lambda root, base: set(),
    )
    pytest_json, vitest = write_coverage_reports(clean_repo, python_pct=40.0)
    code = main(
        [
            "--root",
            str(clean_repo),
            "--base",
            "dagent",
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "new-file floor" in captured.err


def test_coverage_rejects_missing_engineering_source(clean_repo: Path, capsys) -> None:
    extra = clean_repo / "scripts" / "engineering" / "extra.py"
    extra.write_text("X = 1\n", encoding="utf-8")
    pytest_json, vitest = write_coverage_reports(clean_repo)
    code = main(
        [
            "--root",
            str(clean_repo),
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "scripts/engineering/extra.py" in captured.err
    assert "missing from pytest coverage JSON" in captured.err


def test_coverage_rejects_malformed_report(clean_repo: Path, capsys) -> None:
    pytest_json, vitest = write_coverage_reports(clean_repo)
    pytest_json.write_text("{not-json", encoding="utf-8")
    code = main(
        [
            "--root",
            str(clean_repo),
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "malformed JSON" in captured.err


def test_coverage_rejects_historical_file_without_measured_floor(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.coverage._historical_paths",
        lambda root, base: {"src/xagent/app.py", "frontend/src/lib/ok.ts"},
    )
    pytest_json, vitest = write_coverage_reports(clean_repo, python_pct=90.0)
    code = main(
        [
            "--root",
            str(clean_repo),
            "--base",
            "dagent",
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "unknown remains unknown" in captured.err
    assert "src/xagent/app.py" in captured.err


def test_coverage_rejects_historical_file_below_measured_floor(
    clean_repo: Path, capsys
) -> None:
    reference = git_init_and_commit(clean_repo)
    floors = clean_repo / ".run" / "measured-floors.json"
    floors.parent.mkdir(parents=True, exist_ok=True)
    floors.write_text(
        json.dumps(
            {
                "reference": reference,
                "source_hash": run_git(
                    clean_repo, "rev-parse", f"{reference}^{{tree}}"
                ).strip(),
                "files": {
                    "src/xagent/app.py": 92.0,
                    "scripts/engineering/gate.py": 100.0,
                    "frontend/src/lib/ok.ts": 90.0,
                    "frontend/src/components/widget/public-agent-chat-page.tsx": 90.0,
                },
            }
        ),
        encoding="utf-8",
    )
    pytest_json, vitest = write_coverage_reports(clean_repo, python_pct=90.0)
    code = main(
        [
            "--root",
            str(clean_repo),
            "--base",
            reference,
            "coverage",
            "--artifact-base",
            reference,
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
            "--measured-floors",
            str(floors),
        ]
    )
    assert code == 1
    assert "src/xagent/app.py" in capsys.readouterr().err


def test_coverage_rejects_measured_floors_without_provenance(
    clean_repo: Path, capsys
) -> None:
    floors = clean_repo / ".run" / "measured-floors.json"
    floors.parent.mkdir(parents=True, exist_ok=True)
    floors.write_text(
        json.dumps({"files": {"src/xagent/app.py": 90.0}}), encoding="utf-8"
    )
    pytest_json, vitest = write_coverage_reports(clean_repo)
    code = main(
        [
            "--root",
            str(clean_repo),
            "--base",
            "dagent",
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
            "--measured-floors",
            str(floors),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "explicit reference provenance" in captured.err


def test_coverage_rejects_measured_floors_mismatched_base(
    clean_repo: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(
        "scripts.engineering.coverage.resolve_reference", lambda *_: "base-sha"
    )
    floors = clean_repo / ".run" / "measured-floors.json"
    floors.parent.mkdir(parents=True, exist_ok=True)
    floors.write_text(
        json.dumps(
            {
                "reference": "abc123",
                "source_hash": "deadbeef",
                "files": {"src/xagent/app.py": 90.0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.engineering.coverage._historical_paths",
        lambda root, base: {"src/xagent/app.py"},
    )
    pytest_json, vitest = write_coverage_reports(clean_repo)
    code = main(
        [
            "--root",
            str(clean_repo),
            "--base",
            "dagent",
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
            "--measured-floors",
            str(floors),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "does not match --base" in captured.err


def test_coverage_rejects_floors_from_another_source_tree(clean_repo, monkeypatch):
    import pytest

    from scripts.engineering.coverage import _load_measured_floors

    monkeypatch.setattr(
        "scripts.engineering.coverage.resolve_reference", lambda *_: "base-sha"
    )
    monkeypatch.setattr(
        "scripts.engineering.coverage.run_git", lambda *_: "actual-tree"
    )
    floors = clean_repo / "floors.json"
    floors.write_text(
        json.dumps(
            {
                "reference": "base-sha",
                "source_hash": "other-tree",
                "files": {"src/xagent/app.py": 0},
            }
        )
    )
    with pytest.raises(ValueError, match="source_hash"):
        _load_measured_floors(floors, root=clean_repo, base="dagent")


def test_coverage_normalizes_vitest_absolute_keys(clean_repo: Path, capsys) -> None:
    pytest_json, vitest = write_coverage_reports(clean_repo)
    payload = json.loads(vitest.read_text(encoding="utf-8"))
    absolute = {
        "total": payload["total"],
        str((clean_repo / "frontend" / "src" / "lib" / "ok.ts").resolve()): payload[
            "src/lib/ok.ts"
        ],
        str(
            (
                clean_repo
                / "frontend"
                / "src"
                / "components"
                / "widget"
                / "public-agent-chat-page.tsx"
            ).resolve()
        ): payload["src/components/widget/public-agent-chat-page.tsx"],
    }
    vitest.write_text(json.dumps(absolute), encoding="utf-8")
    code = main(
        [
            "--root",
            str(clean_repo),
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "inventoried" in captured.out


def test_coverage_inventory_skips_declarations_and_tests(
    clean_repo: Path, capsys
) -> None:
    (clean_repo / "frontend" / "src" / "types.d.ts").write_text(
        "export {};\n", encoding="utf-8"
    )
    (clean_repo / "frontend" / "src" / "lib" / "utils.test.ts").write_text(
        "export const probe = true;\n", encoding="utf-8"
    )
    (clean_repo / "frontend" / "src" / "vitest.setup.ts").write_text(
        "export {};\n", encoding="utf-8"
    )
    pytest_json, vitest = write_coverage_reports(clean_repo)
    code = main(
        [
            "--root",
            str(clean_repo),
            "coverage",
            "--pytest-json",
            str(pytest_json),
            "--vitest-summary",
            str(vitest),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "types.d.ts" not in captured.err
    assert "utils.test.ts" not in captured.err
    assert "vitest.setup.ts" not in captured.err
