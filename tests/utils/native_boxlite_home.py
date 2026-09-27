"""Short, per-module native SDK homes for real offline sandbox integration tests."""

import fcntl
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from xagent.sandbox.boxlite_bootstrap import prepare_bootstrap

BOOTSTRAP_OCI_ENV = "XAGENT_TEST_BOXLITE_BOOTSTRAP_OCI"


@pytest.fixture(scope="module")
def isolated_native_boxlite_home():
    """Seed a real transferred Debian OCI image, never a synthetic test layer."""
    source = os.environ.get(BOOTSTRAP_OCI_ENV)
    if not source:
        raise RuntimeError(
            f"{BOOTSTRAP_OCI_ENV} must point to the transferred Debian bootstrap OCI "
            "layout for native sandbox execution"
        )
    # Short Unix socket paths; retain the home if teardown detects a live owner.
    directory = Path(tempfile.mkdtemp(prefix="xbxl-", dir="/tmp"))
    home = directory / "home"
    prepare_bootstrap(Path(source), home)
    previous_home = os.environ.get("BOXLITE_HOME_DIR")
    os.environ["BOXLITE_HOME_DIR"] = str(home)
    try:
        yield home
    finally:
        if previous_home is None:
            os.environ.pop("BOXLITE_HOME_DIR", None)
        else:
            os.environ["BOXLITE_HOME_DIR"] = previous_home
        from xagent.sandbox.boxlite_sandbox import _runtimes, _runtimes_lock

        with _runtimes_lock:
            runtime = _runtimes.pop(home, None)
        if runtime is not None:
            # SDK 0.9.7 close() is a no-op; dropping the last owner releases .lock.
            runtime.close()
            del runtime
        with (home / ".lock").open("rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    f"Native runtime still owns retained home {home}"
                ) from exc
            fcntl.flock(lock, fcntl.LOCK_UN)
        shutil.rmtree(directory)
