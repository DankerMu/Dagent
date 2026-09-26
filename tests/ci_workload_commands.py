"""Command-position parsing for CI workload contract checks.

Substring membership is not execution: ``echo python -m pytest``,
``if false; then python -m pytest; fi``, ``true || python -m pytest`` and a
function body that is never called all contain the workload and none of them
runs it. Quoted literals and comments go first, then a command counts only at
nesting depth zero and only from the start of a command.

This is a supported-command-shape contract, not a shell interpreter. Workloads
must run in the foreground without conditional chaining or failure swallowing.
Heredoc bodies are outside this grammar and fail closed.
"""

from __future__ import annotations

import re

# Words that open or close a compound statement, and their effect on nesting.
# `if false; then <workload>; fi` is not proof that the workload runs.
_BLOCK_WORDS = {
    "if": 1,
    "while": 1,
    "until": 1,
    "for": 1,
    "case": 1,
    "select": 1,
    "{": 1,
    "fi": -1,
    "done": -1,
    "esac": -1,
    "}": -1,
    "then": 0,
    "else": 0,
    "elif": 0,
    "do": 0,
    "in": 0,
    "(": 0,
    ")": 0,
}

_SEPARATOR = re.compile(r"\|\||&&|[;\n|&]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# The one wrapper a workload is legitimately launched through, so the check
# cannot be satisfied by another command that merely mentions pytest.
WORKLOAD_RUNNER_PREFIXES = ("python3 -m uv run ",)


def top_level_commands(script: str) -> list[str]:
    """The commands a `run:` script reaches unconditionally, in command position."""
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "", script)
    text = re.sub(r"#[^\n]*", "", text)
    text = re.sub(r"\\\n", " ", text)
    if "<<" in text:
        return []

    commands: list[str] = []
    depth = 0
    preceded_by = ""
    cursor = 0
    for match in [*_SEPARATOR.finditer(text), None]:
        segment = text[cursor : match.start()] if match else text[cursor:]
        words = segment.split()
        while words and words[0] in _BLOCK_WORDS:
            depth = max(depth + _BLOCK_WORDS[words.pop(0)], 0)
        if words and words[-1] == "{":
            # `run_tests() { ... }` defines the workload, it does not call it.
            depth += 1
            words.pop()
        following = match.group(0) if match else ""
        if (
            words
            and depth == 0
            and preceded_by not in {"||", "&&", "|", "&"}
            and following not in {"||", "&&", "|", "&"}
        ):
            commands.append(" ".join(words))
        if match:
            preceded_by = match.group(0)
            cursor = match.end()
    return commands


def invokes(segment: str, command: str) -> bool:
    words = segment.split()
    while words and _ASSIGNMENT.match(words[0]):
        words.pop(0)
    text = " ".join(words)
    for prefix in WORKLOAD_RUNNER_PREFIXES:
        text = text.removeprefix(prefix)
    return text == command or text.startswith(f"{command} ")
