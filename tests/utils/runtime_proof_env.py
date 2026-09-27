"""Runtime-proof flag and .env selection for pytest collection.

Called from ``tests/conftest.py`` at module import, after the existing
xagent imports and before fixtures, so isolated proofs skip developer
``.env`` overwrite at the same moment as the inlined block did.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

RUNTIME_PROOF_ENV = "XAGENT_RUNTIME_PROOF"
RUNTIME_PROOF_MODES = frozenset({"real-model", "ui", "smoke"})
REAL_MODEL_OPTION = "--real-model"
UI_SMOKE_OPTION = "--ui-smoke"

# tests/utils/runtime_proof_env.py -> tests/utils -> tests -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]


def runtime_proof_opt_in(argv: list[str] | None = None) -> bool:
    """True when an isolated runtime proof is selected via env or CLI."""
    selected = os.getenv(RUNTIME_PROOF_ENV, "").strip()
    args = sys.argv if argv is None else argv
    return selected in RUNTIME_PROOF_MODES or (
        REAL_MODEL_OPTION in args or UI_SMOKE_OPTION in args
    )


def load_project_dotenv_unless_runtime_proof(
    project_root: Path | None = None,
    *,
    argv: list[str] | None = None,
) -> None:
    """Load ``.env`` / ``example.env`` unless a runtime proof is opted in.

    Isolated proofs must not inherit developer credentials or DATABASE_URL
    at collection time. Existing suites still load ``.env``.
    """
    if runtime_proof_opt_in(argv):
        return
    root = _REPO_ROOT if project_root is None else project_root
    env_file = root / ".env"
    example_env_file = root / "example.env"
    if env_file.exists():
        load_dotenv(env_file, override=True)  # Force override existing env vars
    elif example_env_file.exists():
        # Don't override existing env vars (especially API keys from user's shell)
        load_dotenv(example_env_file, override=False)
    else:
        print("Warning: Neither .env nor example.env file found")


def add_runtime_proof_options(parser) -> None:
    parser.addoption(
        REAL_MODEL_OPTION,
        action="store_true",
        default=False,
        help="Run real-model proofs (requires explicit OPENAI_BASE_URL and OPENAI_MODEL)",
    )
    parser.addoption(
        UI_SMOKE_OPTION,
        action="store_true",
        default=False,
        help="Run browser UI smoke proofs (requires Playwright Chromium)",
    )


def register_runtime_proof_markers(config) -> None:
    config.addinivalue_line(
        "markers",
        "real_model: local real-model proofs selected by --real-model or "
        "XAGENT_RUNTIME_PROOF=real-model",
    )
    config.addinivalue_line(
        "markers",
        "ui_smoke: browser UI proofs selected by --ui-smoke or XAGENT_RUNTIME_PROOF=ui",
    )


def selected_runtime_proofs(config) -> tuple[bool, bool]:
    selected = os.getenv(RUNTIME_PROOF_ENV, "").strip()
    run_real_model = config.getoption(REAL_MODEL_OPTION, default=False) or (
        selected == "real-model"
    )
    run_ui_smoke = config.getoption(UI_SMOKE_OPTION, default=False) or (
        selected == "ui"
    )
    return run_real_model, run_ui_smoke


def deselect_unselected_runtime_proofs(config, items) -> tuple[bool, bool]:
    """Drop unselected real_model / ui_smoke items. Returns the opt-in flags."""
    run_real_model, run_ui_smoke = selected_runtime_proofs(config)
    if not run_real_model:
        items[:] = [item for item in items if "real_model" not in item.keywords]
    if not run_ui_smoke:
        items[:] = [item for item in items if "ui_smoke" not in item.keywords]
    return run_real_model, run_ui_smoke
