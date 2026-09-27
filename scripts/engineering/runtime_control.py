"""Portable process identity and fail-closed pytest proof helpers."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psutil

STOP_TIMEOUT_SECONDS = 30
PROOF_TIMEOUT_SECONDS = 600
_PATH_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
)
_RUNTIME_KEYS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "UV_PROJECT",
    "UV_PYTHON",
)
_ASSET_PATH_KEYS = (
    "TIKTOKEN_CACHE_DIR",
    "DATA_GYM_CACHE_DIR",
    "DEEPDOC_TIKTOKEN_CACHE_DIR",
    "DEEPDOC_MODEL_HOME",
    "DEEPDOC_NLTK_DATA_DIR",
    "NLTK_DATA",
)


class ProcessIdentityError(RuntimeError):
    """Raised when a recorded process cannot be verified as the owned process."""


def process_identity(pid: int) -> dict[str, Any]:
    process = psutil.Process(pid)
    return {
        "pid": pid,
        "create_time": process.create_time(),
        "exe": process.exe(),
        "cmdline": process.cmdline(),
    }


def verify_process(identity: dict[str, Any] | None) -> psutil.Process:
    if not identity or not identity.get("pid"):
        raise ProcessIdentityError("missing process identity")
    try:
        process = psutil.Process(int(identity["pid"]))
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError) as exc:
        raise ProcessIdentityError("process does not exist") from exc
    if process.create_time() != identity.get("create_time"):
        raise ProcessIdentityError("PID reused; create_time mismatch")
    if identity.get("exe") and process.exe() != identity["exe"]:
        raise ProcessIdentityError("PID reused; executable mismatch")
    return process


def terminate_tree(
    identity: dict[str, Any] | None, timeout: float = STOP_TIMEOUT_SECONDS
) -> None:
    if not identity:
        return
    try:
        process = verify_process(identity)
    except ProcessIdentityError:
        return
    children = process.children(recursive=True)
    try:
        process.send_signal(signal.SIGTERM)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return
    for child in children:
        try:
            child.send_signal(signal.SIGTERM)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    gone, alive = psutil.wait_procs([process, *children], timeout=timeout)
    for remaining in alive:
        try:
            remaining.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(alive, timeout=5)
    _ = gone


def prepared_asset_env(parent: Mapping[str, str]) -> dict[str, str]:
    """Forward only prepared local asset paths, retaining explicit empty values."""
    return {key: parent[key] for key in _ASSET_PATH_KEYS if key in parent}


def allowlisted_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in _PATH_KEYS + _RUNTIME_KEYS:
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment.update(prepared_asset_env(os.environ))
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    environment["LANGFUSE_TRACING_ENABLED"] = "false"
    if extra:
        environment.update(extra)
    return environment


def junit_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        raise ValueError(f"missing junit report {path}")
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites and root.tag == "testsuites":
        suites = list(root)
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.attrib.get("tests") or 0)
        failures += int(suite.attrib.get("failures") or 0)
        errors += int(suite.attrib.get("errors") or 0)
        skipped += int(suite.attrib.get("skipped") or 0)
    executed = tests - skipped
    return {
        "tests": tests,
        "executed": executed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def assert_zero_skipped(counts: dict[str, int]) -> None:
    if counts["tests"] <= 0 or counts["executed"] <= 0:
        raise SystemExit("Selected proof collected zero tests")
    if counts["skipped"] != 0:
        raise SystemExit(
            "Selected proof skipped tests; missing credentials or tools must fail"
        )
    if counts["failures"] != 0 or counts["errors"] != 0:
        raise SystemExit("Selected proof reported failures or errors")


def run_proof_pytest(
    args: list[str],
    *,
    extra_env: dict[str, str],
    basetemp: Path,
    junit_path: Path,
    cwd: Path,
    timeout: float = PROOF_TIMEOUT_SECONDS,
) -> int:
    basetemp.mkdir(parents=True, exist_ok=True)
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    environment = allowlisted_env(extra_env)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *args,
        f"--basetemp={basetemp}",
        f"--junitxml={junit_path}",
        "-q",
        "-ra",
        "-n",
        "0",
        "--tb=short",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    identity = process_identity(process.pid)
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_tree(identity)
        process.wait(timeout=5)
        raise SystemExit("Selected proof timed out")
    sys.stdout.write(stdout or "")
    try:
        counts = junit_counts(junit_path)
    except (ET.ParseError, ValueError, OSError) as exc:
        raise SystemExit(f"Selected proof did not write a JUnit report: {exc}") from exc
    assert_zero_skipped(counts)
    return process.returncode
