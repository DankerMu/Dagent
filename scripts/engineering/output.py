"""Error-message protocol for engineering gates."""

from __future__ import annotations

import sys

VIOLATION = 1
USAGE_ERROR = 2
TOOL_FAILURE = 2


def report(gate: str, errors: list[str], summary: str) -> int:
    """Print the protocol-shaped result and return the exit code."""
    if errors:
        print(f"{gate}: {len(errors)} violation(s) found:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return VIOLATION
    print(f"{gate}: {summary}")
    return 0
