"""Short, per-module native SDK homes for real offline sandbox integration tests."""

import os
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
    # The SDK uses Unix-domain sockets; pytest's default macOS temp path is too long.
    with tempfile.TemporaryDirectory(prefix="xbxl-", dir="/tmp") as directory:
        home = Path(directory) / "home"
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
            # Services retain their SDK runtime per home. Release its lock before
            # TemporaryDirectory removes the home and its native guest state.
            from xagent.sandbox.boxlite_sandbox import _runtimes, _runtimes_lock

            with _runtimes_lock:
                runtime = _runtimes.pop(home, None)
            if runtime is not None:
                runtime.close()
