"""Shared fixtures for engineering gate tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def write_constraints(root: Path, **overrides: object) -> None:
    data = yaml.safe_load((REPO_ROOT / "constraints.yaml").read_text(encoding="utf-8"))
    for key, value in overrides.items():
        target = data
        parts = key.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    (root / "constraints.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def write_makefile(root: Path) -> None:
    (root / "Makefile").write_text(
        textwrap.dedent(
            """
            .PHONY: check test-guardrails test-ci-contracts test-logging build smoke verify-ui verify-real-model test-integration guardrails docs-check coverage security diff-check lint typecheck
            check:
            	@true
            test-guardrails:
            	@true
            test-ci-contracts:
            	@true
            test-logging:
            	@true
            build:
            	@true
            smoke:
            	@true
            verify-ui:
            	@true
            verify-real-model:
            	@true
            test-integration:
            	@true
            guardrails:
            	@true
            docs-check:
            	@true
            coverage:
            	@true
            security:
            	@true
            diff-check:
            	@true
            lint:
            	@true
            typecheck:
            	@true
            """
        ).lstrip(),
        encoding="utf-8",
    )


def write_docs(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        textwrap.dedent(
            """
            # Fixture

            ## Code Canonicality

            One implementation.

            ## Project Identity

            Fixture project.

            ## Stack & Versions

            Python and TypeScript.

            ## Directory Map

            `src/` and `frontend/src/`.

            ## Development Workflow

            | Task | Command |
            |---|---|
            | Checks | `make check` |

            ## Verification Matrix

            | Surface | Command | Evidence |
            |---|---|---|
            | Engineering constraints | `make check` | gates |
            | Gate discrimination | `make test-guardrails` | accept/reject |
            | CI failure propagation | `make test-ci-contracts` | skipped is red |
            | Structured logging | `make test-logging` | json |
            | Build | `make build` | artifacts |
            | Health and API behavior | `make smoke` | http |
            | Browser behavior | `make verify-ui` | screenshot |
            | Real model task | `make verify-real-model` | task |
            | Real database behavior | `make test-integration` | db |

            ## Runtime Lifecycle

            Isolated runtime.

            ## Important Development Notes

            - 2026-09-25: fixture

            ## Conventions

            Conventional commits.

            ## Code Review Self-Check

            Explain the change.

            ## Architecture Discipline

            AgentService owns execution.

            ## Critical Paths

            Auth.

            ## Agent Operating Rules

            One issue at a time.

            ## Enforcement Index

            | Control | Authority / executable | Level |
            |---|---|---|
            | L3 thresholds | `scripts/engineering/check.py`; `make guardrails` | block |
            | Coverage | `scripts/engineering/check.py`; `make coverage` | block |
            | Secret scanning / SAST | `scripts/engineering/check.py`; `make security` | block |
            | Docs | `scripts/engineering/check.py`; `make docs-check` | block |
            | Gate self-proof | `scripts/test-guardrails.sh`; `make test-guardrails` | block |
            | PR size | `constraints.yaml`; `make diff-check BASE=dagent` | block |

            ## Known Limitations and Deferred Work

            Fixture limitations.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (root / "CONTEXT.md").write_text(
        textwrap.dedent(
            """
            # Context

            ## Domain Language

            Confirmed definitions live only in `openspec/glossary.md`.

            ## Bounded Contexts

            Web and agent execution.

            ## Known Limitations and Deferred Work

            Fixture limitations.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    glossary = root / "openspec" / "glossary.md"
    glossary.parent.mkdir(parents=True, exist_ok=True)
    glossary.write_text(
        textwrap.dedent(
            """
            # Glossary

            ## Language

            **Agent**:
            An actor.

            **Task**:
            One execution.

            **Memory**:
            User memory.

            **Knowledge Base (KB/RAG)**:
            Document grounding.

            **Workspace**:
            Isolated files.
            """
        ).lstrip(),
        encoding="utf-8",
    )


def write_source(root: Path) -> None:
    module = root / "src" / "xagent" / "app.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "def greet(name: str) -> str:\n    return f'hi {name}'\n", encoding="utf-8"
    )
    gate = root / "scripts" / "engineering" / "gate.py"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text("VALUE = 1\n", encoding="utf-8")
    frontend = root / "frontend" / "src" / "lib" / "ok.ts"
    frontend.parent.mkdir(parents=True, exist_ok=True)
    frontend.write_text("export const ok = true;\n", encoding="utf-8")
    widget = (
        root
        / "frontend"
        / "src"
        / "components"
        / "widget"
        / "public-agent-chat-page.tsx"
    )
    widget.parent.mkdir(parents=True, exist_ok=True)
    widget.write_text("export function Page() { return null; }\n", encoding="utf-8")
    (root / "frontend" / "knip.json").write_text(
        (REPO_ROOT / "frontend" / "knip.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _copy(REPO_ROOT / ".semgrep.yml", root / ".semgrep.yml")


def write_coverage_reports(
    root: Path, *, python_pct: float = 90.0, frontend_pct: float = 90.0
) -> tuple[Path, Path]:
    fraction = Fraction(str(python_pct)) / 100
    py_path = root / ".run" / "coverage-python.json"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/xagent/app.py": {
                        "summary": {
                            "covered_lines": fraction.numerator,
                            "num_statements": fraction.denominator,
                            "percent_covered": python_pct,
                            "missing_lines": fraction.denominator - fraction.numerator,
                        }
                    },
                    "scripts/engineering/gate.py": {
                        "summary": {
                            "covered_lines": 1,
                            "num_statements": 1,
                            "percent_covered": 100.0,
                            "missing_lines": 0,
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    ts_path = root / ".run" / "coverage-frontend" / "coverage-summary.json"
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "total": {
            "lines": {"pct": 99.0},
            "statements": {"pct": 99.0},
            "branches": {"pct": 99.0},
            "functions": {"pct": 99.0},
        },
        "src/lib/ok.ts": {
            "lines": {"pct": frontend_pct},
            "statements": {"pct": frontend_pct},
            "branches": {"pct": frontend_pct},
            "functions": {"pct": frontend_pct},
        },
        "src/components/widget/public-agent-chat-page.tsx": {
            "lines": {"pct": 80.0},
            "statements": {"pct": 80.0},
            "branches": {"pct": 55.0},
            "functions": {"pct": 45.0},
        },
    }
    ts_path.write_text(json.dumps(payload), encoding="utf-8")
    return py_path, ts_path


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    write_constraints(tmp_path)
    write_makefile(tmp_path)
    write_docs(tmp_path)
    write_source(tmp_path)
    return tmp_path


def git_init_and_commit(root: Path, message: str = "init") -> str:
    """Create a real git repo at root and commit tracked files. Returns HEAD sha."""
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Engineering Tests",
            "GIT_AUTHOR_EMAIL": "engineering-tests@example.com",
            "GIT_COMMITTER_NAME": "Engineering Tests",
            "GIT_COMMITTER_EMAIL": "engineering-tests@example.com",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
    )
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=root,
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=root,
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Engineering Tests",
            "-c",
            "user.email=engineering-tests@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=root,
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def git_repo(clean_repo: Path) -> tuple[Path, str]:
    sha = git_init_and_commit(clean_repo)
    return clean_repo, sha
