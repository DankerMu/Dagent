#!/usr/bin/env python3
"""Engineering gate CLI. Read-only unless baseline --write --reference is set."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "engineering"

from .baseline import run_baseline
from .coverage import check_coverage
from .diffcheck import check_diff
from .docs import check_docs
from .guardrails import check_guardrails
from .output import USAGE_ERROR
from .security import check_security


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check.py",
        description="L3 engineering gates. CI is read-only; baseline writes need --write and --reference.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (fixture tests pass this explicitly)",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="PR base for diff/static gates; frozen measurement reference for coverage",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "guardrails",
        help="size, complexity, duplication, dead code, naming, scratch dirs",
    )
    sub.add_parser(
        "docs",
        help="AGENTS command targets, verification mirror, ownership, exemptions",
    )

    coverage = sub.add_parser(
        "coverage", help="per-file coverage against actual reports"
    )
    coverage.add_argument("--pytest-json", type=Path, default=None)
    coverage.add_argument("--vitest-summary", type=Path, default=None)
    coverage.add_argument(
        "--scope",
        choices=("all", "python", "frontend"),
        default="all",
        help="all requires both reports; python/frontend require only the selected report",
    )
    coverage.add_argument(
        "--measured-floors",
        type=Path,
        default=None,
        help="JSON with reference, source_hash, and files path->percent from the --base report",
    )
    coverage.add_argument(
        "--artifact-base",
        default=None,
        help="actual PR base anchoring measured-floor bytes, independent of frozen --base",
    )

    security = sub.add_parser("security", help="detect-secrets plus Semgrep (CI)")
    security.add_argument(
        "--secrets-only",
        action="store_true",
        help="run detect-secrets only (pre-commit secrets-check)",
    )
    sub.add_parser("secrets", help="alias for security --secrets-only")
    sub.add_parser("diff", help="PR diff size against --base; approved bootstrap only")

    baseline = sub.add_parser("baseline", help="compare or capture frozen findings")
    baseline.add_argument(
        "--write", action="store_true", help="write .engineering/baseline.json"
    )
    baseline.add_argument(
        "--reference",
        default=None,
        help="pristine git SHA or ref to archive before scanning",
    )
    return parser


def repo_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    here = Path(__file__).resolve()
    return here.parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = repo_root(args.root)
    command = args.command
    if command == "guardrails":
        return check_guardrails(root, args.base)
    if command == "docs":
        return check_docs(root)
    if command == "coverage":
        return check_coverage(
            root,
            args.pytest_json,
            args.vitest_summary,
            args.base,
            args.measured_floors,
            args.scope,
            args.artifact_base,
        )
    if command == "security":
        return check_security(root, args.base, secrets_only=args.secrets_only)
    if command == "secrets":
        return check_security(root, args.base, secrets_only=True)
    if command == "diff":
        return check_diff(root, args.base)
    if command == "baseline":
        return run_baseline(
            root,
            args.base,
            write=args.write,
            reference=args.reference,
        )
    print(f"unknown command {command}", file=sys.stderr)
    return USAGE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
