"""Coverage debt is anchored separately from its historical source revision."""

import json

import pytest

from scripts.engineering.check import main
from scripts.engineering.gitutil import run_git
from tests.engineering.conftest import git_init_and_commit, write_coverage_reports


@pytest.mark.parametrize("tamper", ["lower_floor", "omit_authority"])
def test_candidate_cannot_rewrite_measured_floors(git_repo, tamper):
    root, reference = git_repo
    artifact = root / ".engineering/coverage-floors.json"
    artifact.parent.mkdir(exist_ok=True)
    payload = {
        "reference": reference,
        "source_hash": run_git(root, "rev-parse", f"{reference}^{{tree}}").strip(),
        "files": {"src/xagent/app.py": 90, "scripts/engineering/gate.py": 100},
    }
    artifact.write_text(json.dumps(payload))
    artifact_base = git_init_and_commit(root, "freeze measured coverage")
    report, _ = write_coverage_reports(root)
    args = [
        "--root",
        str(root),
        "--base",
        reference,
        "coverage",
        "--scope",
        "python",
        "--pytest-json",
        str(report),
        "--measured-floors",
        str(artifact.relative_to(root)),
    ]
    authority = ["--artifact-base", artifact_base]
    assert main(args + authority) == 0

    if tamper == "lower_floor":
        payload["files"]["src/xagent/app.py"] = 80
        artifact.write_text(json.dumps(payload))
        write_coverage_reports(root, python_pct=80)
    else:
        authority = []

    assert main(args + authority) != 0
