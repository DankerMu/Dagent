"""A scanner schema change must not silently erase duplication findings."""

from pathlib import Path

import pytest

from scripts.engineering.adapters import parse_jscpd_payload
from scripts.engineering.config import load_constraints
from scripts.engineering.tools import ToolFailure
from tests.engineering.conftest import write_constraints


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"duplicates": []},
        {"statistics": {}},
        {"statistics": {"total": {"percentage": 0}}},
        {"statistics": {"total": {"percentage": 0}}, "duplicates": None},
    ],
)
def test_incomplete_jscpd_report_is_not_a_clean_scan(clean_repo, payload):
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(clean_repo, load_constraints(clean_repo), payload)


@pytest.mark.parametrize("percentage", [True, float("nan"), float("inf"), -1, 101])
def test_invalid_jscpd_percentage_is_not_a_clean_scan(clean_repo, percentage):
    payload = {"statistics": {"total": {"percentage": percentage}}, "duplicates": []}
    with pytest.raises(ToolFailure):
        parse_jscpd_payload(clean_repo, load_constraints(clean_repo), payload)


def test_complete_empty_jscpd_report_is_clean(clean_repo):
    payload = {"statistics": {"total": {"percentage": 0}}, "duplicates": []}
    assert parse_jscpd_payload(clean_repo, load_constraints(clean_repo), payload) == []


def test_jscpd_clone_startloc_and_exempt_pair(clean_repo: Path) -> None:
    write_constraints(
        clean_repo,
        **{
            "exemptions.entries": [
                {
                    "path": "src/a.py",
                    "rules": ["duplicate_code"],
                    "reason": "generated pair",
                    "exit_condition": "delete",
                },
                {
                    "path": "src/b.py",
                    "rules": ["duplicate_code"],
                    "reason": "generated pair",
                    "exit_condition": "delete",
                },
            ]
        },
    )
    constraints = load_constraints(clean_repo)
    skipped = parse_jscpd_payload(
        clean_repo,
        constraints,
        {
            "statistics": {"total": {"percentage": 0}},
            "duplicates": [
                {
                    "firstFile": {
                        "name": "src/a.py",
                        "startLoc": {"line": 8},
                    },
                    "secondFile": {"name": "src/b.py"},
                }
            ],
        },
    )
    assert skipped == []
    findings = parse_jscpd_payload(
        clean_repo,
        constraints,
        {
            "statistics": {"total": {"percentage": 0}},
            "duplicates": [
                {
                    "firstFile": {
                        "name": "src/xagent/app.py",
                        "startLoc": {"line": 11},
                    },
                    "secondFile": {"name": "src/xagent/other.py"},
                    "fragment": "one\ntwo\nthree\nfour\nfive\n",
                }
            ],
        },
    )
    assert findings[0].line == 11
    assert "src/xagent/other.py" in findings[0].detail
    shifted = parse_jscpd_payload(
        clean_repo,
        constraints,
        {
            "statistics": {"total": {"percentage": 0}},
            "duplicates": [
                {
                    "firstFile": {
                        "name": "src/xagent/app.py",
                        "startLoc": {"line": 40},
                    },
                    "secondFile": {"name": "src/xagent/other.py"},
                    "fragment": "one\ntwo\nthree\nfour\nfive\n",
                }
            ],
        },
    )
    assert shifted[0].fingerprint == findings[0].fingerprint
    assert shifted[0].line == 40
