"""Documentation discipline gate."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .config import ConfigError, Constraints, load_constraints
from .output import TOOL_FAILURE, report

HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
BACKTICK_COMMAND = re.compile(r"`([^`]+)`")
MAKE_COMMAND_RE = re.compile(r"\bmake\s+[A-Za-z0-9_.%-]+")
LIMITATIONS_HEADING = "Known Limitations and Deferred Work"
DOMAIN_HEADING = "Domain Language"
GLOSSARY_PATH = "openspec/glossary.md"
CONTEXT_PATH = "CONTEXT.md"
AGENTS_PATH = "AGENTS.md"
MAKE_RECIPE_RE = re.compile(r"(?m)^([A-Za-z0-9_.%/-][A-Za-z0-9_.%/-]*)\s*:(?!=)")


def _section(markdown: str, title: str) -> str:
    pattern = rf"(?ms)^## {re.escape(title)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, markdown)
    return match.group("body") if match else ""


def _table_commands(markdown: str) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or cells[0].lower() in {"surface", "task", "control", "action"}:
            continue
        for cell in cells:
            for command in BACKTICK_COMMAND.findall(cell):
                text = command.strip()
                if text.startswith("make ") and text not in seen:
                    commands.append(text)
                    seen.add(text)
            for match in MAKE_COMMAND_RE.finditer(cell):
                text = match.group(0)
                if text not in seen:
                    commands.append(text)
                    seen.add(text)
    return commands


def _make_targets(root: Path) -> set[str]:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return set()
    targets: set[str] = set()
    for match in MAKE_RECIPE_RE.finditer(makefile.read_text(encoding="utf-8")):
        for target in match.group(1).split():
            if target and not target.startswith("."):
                targets.add(target)
    return targets


def _command_target(command: str) -> str | None:
    parts = command.split()
    if len(parts) >= 2 and parts[0] == "make":
        return parts[1]
    return None


def _definition_terms(glossary: str) -> set[str]:
    terms: set[str] = set()
    for match in re.finditer(r"(?m)^\*\*(.+?)\*\*:\s*$", glossary):
        terms.add(match.group(1).strip())
    return terms


def _exemption_errors(root: Path, constraints: Constraints) -> list[str]:
    errors: list[str] = []
    for entry in constraints.exemption_entries:
        target = str(entry.get("path") or "")
        reason = str(entry.get("reason") or "").strip()
        if not target:
            errors.append("exemption is missing path; name an existing target")
            continue
        if not reason or reason.lower() in {"because it fails", "n/a"}:
            errors.append(
                f"{target}  exemption reason is empty or non-justifying; "
                "update constraints.yaml exemptions in the same change"
            )
        path = root / target
        if not path.exists():
            errors.append(
                f"{target}  exemption target was renamed or removed; "
                "update constraints.yaml exemptions in the same change"
            )
    return errors


def _mirror_errors(
    matrix_commands: list[str],
    constraints: Constraints,
    targets: set[str],
) -> list[str]:
    errors: list[str] = []
    mirrored = set(constraints.verification_commands)
    for command in matrix_commands:
        if command not in mirrored:
            errors.append(
                f"AGENTS.md  Verification Matrix command `{command}` is not mirrored "
                "in constraints.yaml verification.surfaces"
            )
    for command in constraints.verification_commands:
        if command not in set(matrix_commands):
            errors.append(
                f"constraints.yaml  verification command `{command}` is not present "
                "in AGENTS.md Verification Matrix"
            )
        target = _command_target(command)
        if target and target not in targets:
            errors.append(
                f"constraints.yaml  verification command `{command}` has no Makefile target"
            )
    return errors


def _command_errors(
    agents: str,
    constraints: Constraints,
    targets: set[str],
) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    matrix = _section(agents, "Verification Matrix")
    index = _section(agents, "Enforcement Index")
    if not matrix:
        errors.append("AGENTS.md  missing ## Verification Matrix")
    if not index:
        errors.append("AGENTS.md  missing ## Enforcement Index")
    matrix_commands = _table_commands(matrix)
    documented: list[str] = []
    seen: set[str] = set()
    workflow = _section(agents, "Development Workflow")
    for command in matrix_commands + _table_commands(index) + _table_commands(workflow):
        if command not in seen:
            documented.append(command)
            seen.add(command)
    for command in documented:
        target = _command_target(command)
        if target is not None and target not in targets:
            errors.append(
                f"AGENTS.md  command `{command}` has no Makefile target {target}; "
                "add the recipe or update the matrix"
            )
    errors.extend(_mirror_errors(matrix_commands, constraints, targets))
    required = (
        "scripts/engineering/check.py",
        "make guardrails",
        "make docs-check",
        "make coverage",
        "make security",
        "make diff-check",
        "make test-guardrails",
    )
    if any(token not in index for token in required):
        errors.append(
            "AGENTS.md Enforcement Index must name scripts/engineering/check.py and "
            "make guardrails, docs-check, coverage, security, diff-check, test-guardrails"
        )
    if ".omp" in matrix or "skill://eng-init" in agents:
        errors.append(
            "AGENTS.md  durable checks must not depend on ignored .omp or eng-init skill paths"
        )
    return errors, documented, matrix_commands


def _ownership_errors(
    root: Path,
    constraints: Constraints,
    agents: str,
    context: str,
    glossary: str,
) -> list[str]:
    errors: list[str] = []
    if DOMAIN_HEADING in agents and re.search(
        r"(?ms)^## Domain Language\n(?:.*\n)*?\*\*", agents
    ):
        errors.append(
            "AGENTS.md  must not define domain terms; openspec/glossary.md is the single owner"
        )
    if DOMAIN_HEADING in context:
        body = _section(context, DOMAIN_HEADING)
        if GLOSSARY_PATH not in body and GLOSSARY_PATH not in context:
            errors.append(
                "CONTEXT.md Domain Language must link to openspec/glossary.md instead of defining terms"
            )
        if re.search(r"(?m)^\*\*[^*]+\*\*:\s*$", body):
            errors.append(
                "CONTEXT.md Domain Language defines terms; move them to openspec/glossary.md"
            )
    if glossary and not _definition_terms(glossary):
        errors.append("openspec/glossary.md contains no term definitions")
    for rel in constraints.known_limitation_files:
        path = root / rel
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if f"## {LIMITATIONS_HEADING}" not in text:
            errors.append(
                f"{rel}  missing ## {LIMITATIONS_HEADING}; add the section or update "
                "constraints.yaml documentation.known_limitations"
            )
    errors.extend(_limitation_allowlist_errors(root, constraints))
    return errors


def _limitation_allowlist_errors(root: Path, constraints: Constraints) -> list[str]:
    errors: list[str] = []
    allowlist = (
        constraints.raw.get("documentation", {})
        .get("known_limitations", {})
        .get("allowlist", [])
    )
    if not allowlist:
        return errors
    if not isinstance(allowlist, list):
        errors.append("documentation.known_limitations.allowlist must be a list")
        return errors
    for item in allowlist:
        if not isinstance(item, dict) or not item.get("path") or not item.get("reason"):
            errors.append("Known Limitations allowlist entries need path and reason")
            continue
        if not (root / str(item["path"])).exists():
            errors.append(
                f"{item['path']}  Known Limitations exemption target was renamed or removed"
            )
    return errors


def check_docs(root: Path) -> int:
    errors: list[str] = []
    try:
        constraints = load_constraints(root)
    except ConfigError as exc:
        print(f"docs: {exc}", file=sys.stderr)
        return TOOL_FAILURE
    agents_path = root / AGENTS_PATH
    context_path = root / CONTEXT_PATH
    glossary_path = root / GLOSSARY_PATH
    makefile = root / "Makefile"
    if not agents_path.is_file():
        errors.append("AGENTS.md is missing")
        return report("docs", errors, "documentation conforms")
    if not context_path.is_file():
        errors.append("CONTEXT.md is missing; it owns bounded contexts and invariants")
    if not glossary_path.is_file():
        errors.append(
            "openspec/glossary.md is missing; it owns domain-term definitions"
        )
    if not makefile.is_file():
        errors.append("Makefile is missing; command targets cannot be verified")
        return report("docs", errors, "documentation conforms")
    agents = agents_path.read_text(encoding="utf-8")
    context = context_path.read_text(encoding="utf-8") if context_path.is_file() else ""
    glossary = (
        glossary_path.read_text(encoding="utf-8") if glossary_path.is_file() else ""
    )
    line_count = len(agents.splitlines())
    if line_count > constraints.agents_md_max_lines:
        errors.append(
            f"AGENTS.md:{line_count}  expected at most {constraints.agents_md_max_lines} lines, "
            f"got {line_count}"
        )
    headings = HEADING_RE.findall(agents)
    if headings and headings[0] != "Code Canonicality":
        errors.append(
            'AGENTS.md:1  expected first section "Code Canonicality", '
            f"got {headings[0]!r}"
        )
    for title in constraints.generated_section_titles:
        if f"## {title}" not in agents:
            errors.append(f"AGENTS.md  generated section {title!r} is missing")
    command_errors, documented, _matrix = _command_errors(
        agents, constraints, _make_targets(root)
    )
    errors.extend(command_errors)
    errors.extend(_ownership_errors(root, constraints, agents, context, glossary))
    errors.extend(_exemption_errors(root, constraints))
    if errors:
        return report("docs", errors, "documentation conforms")
    checked = len(documented) + len(constraints.generated_section_titles)
    return report("docs", [], f"{checked} command and ownership checks, all conform")
