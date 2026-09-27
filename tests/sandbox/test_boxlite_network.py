"""Native guest isolation must survive SDK upgrades and guest restart."""

import pytest

from tests.utils import native_boxlite_home
from xagent.config import get_boxlite_rootfs_path
from xagent.sandbox.base import SandboxConfig
from xagent.sandbox.boxlite_sandbox import BoxliteSandboxService, MemBoxliteStore

isolated_native_boxlite_home = native_boxlite_home.isolated_native_boxlite_home
_layout = get_boxlite_rootfs_path()
pytestmark = pytest.mark.skipif(
    not (_layout and _layout.is_dir()), reason="Requires preloaded guest OCI layout"
)


@pytest.mark.asyncio(loop_scope="module")
async def test_isolated_guest_cannot_route_external_traffic_after_restart(
    isolated_native_boxlite_home,
):
    service = BoxliteSandboxService(MemBoxliteStore())
    name = "network-isolation-restart"
    config = SandboxConfig(network_isolated=True)
    probe = """
import errno
import socket
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    try:
        sock.connect(('192.0.2.1', 9))
    except OSError as error:
        assert error.errno == errno.ENETUNREACH, error
    else:
        raise AssertionError('isolated guest has an external route')
print('external-route-denied')
"""
    try:
        sandbox = await service.get_or_create(name, config=config)
        for restart in (False, True):
            if restart:
                await sandbox.stop()
                sandbox = await service.get_or_create(name, config=config)
            result = await sandbox.exec("python", "-c", probe)
            assert result.exit_code == 0, result.stderr
            assert result.stdout.strip() == "external-route-denied"
    finally:
        await service.delete(name)
