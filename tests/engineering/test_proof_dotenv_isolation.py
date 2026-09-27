"""Runtime proof children must not import developer dotenv values."""

import subprocess
import sys

from scripts.engineering.runtime_control import allowlisted_env


def test_proof_child_ignores_even_explicit_dotenv_path(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("OFFLINE_PROOF_SENTINEL=unexpected\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from dotenv import load_dotenv; "
            "assert load_dotenv(sys.argv[1], override=True) is False; "
            "assert 'OFFLINE_PROOF_SENTINEL' not in os.environ",
            str(dotenv_path),
        ],
        cwd=tmp_path,
        env=allowlisted_env(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
