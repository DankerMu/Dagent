"""Missing or non-finite coverage measurements cannot satisfy a floor."""

import json

import pytest

from scripts.engineering.check import main
from tests.engineering.conftest import write_coverage_reports


@pytest.mark.parametrize(
    "summary",
    [
        {},
        {"covered_lines": 1},
        {"num_statements": 1},
        {"covered_lines": True, "num_statements": 1},
        {"covered_lines": 2, "num_statements": 1},
        {"covered_lines": 1, "num_statements": 1, "percent_covered": float("nan")},
    ],
)
def test_malformed_python_row_fails_closed(clean_repo, summary):
    report, _ = write_coverage_reports(clean_repo)
    payload = json.loads(report.read_text())
    payload["files"]["src/xagent/app.py"]["summary"] = summary
    report.write_text(json.dumps(payload))
    assert (
        main(
            [
                "--root",
                str(clean_repo),
                "coverage",
                "--scope",
                "python",
                "--pytest-json",
                str(report),
            ]
        )
        == 2
    )


@pytest.mark.parametrize("pct", [float("nan"), float("inf"), True, 101])
def test_invalid_frontend_percentage_fails_closed(clean_repo, pct):
    _, report = write_coverage_reports(clean_repo)
    payload = json.loads(report.read_text())
    for path, metrics in payload.items():
        if path != "total":
            metrics["lines"]["pct"] = pct
    report.write_text(json.dumps(payload))
    assert (
        main(
            [
                "--root",
                str(clean_repo),
                "coverage",
                "--scope",
                "frontend",
                "--vitest-summary",
                str(report),
            ]
        )
        == 2
    )


def test_python_floor_measures_lines_not_combined_branch_percentage(clean_repo):
    report, _ = write_coverage_reports(clean_repo)
    payload = json.loads(report.read_text())
    payload["files"]["src/xagent/app.py"]["summary"] = {
        "covered_lines": 1,
        "num_statements": 10,
        "missing_lines": 9,
        "percent_covered": 95,
    }
    report.write_text(json.dumps(payload))
    assert (
        main(
            [
                "--root",
                str(clean_repo),
                "coverage",
                "--scope",
                "python",
                "--pytest-json",
                str(report),
            ]
        )
        == 1
    )
