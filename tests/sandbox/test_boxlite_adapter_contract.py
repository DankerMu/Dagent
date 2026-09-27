"""Adapter state transitions, independent of native VM availability.

The stateful engine double models the SDK boundary, not a functioning VM.
Native execution and host mount propagation remain separate integration proofs.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xagent.sandbox import boxlite_sandbox as adapter
from xagent.sandbox.base import SandboxConfig, SandboxTemplate


@pytest.fixture
def engine(monkeypatch, tmp_path, prepared_boxlite_home):
    layout = tmp_path / "sandbox-oci"
    layout.mkdir()
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (layout / "index.json").write_text('{"schemaVersion":2,"manifests":[]}')
    (layout / "blobs").mkdir()
    monkeypatch.setenv("XAGENT_BOXLITE_ROOTFS_PATH", str(layout))
    monkeypatch.setenv("BOXLITE_HOME_DIR", str(prepared_boxlite_home))
    records = {}

    class Runtime:
        async def get(self, name):
            return (
                SimpleNamespace(info=lambda: records[name]) if name in records else None
            )

        async def get_info(self, name):
            return records[name]

        async def list_info(self):
            return list(records.values())

        async def remove(self, name, force):
            del records[name]

    runtime = Runtime()

    class Box:
        def __init__(self, **options):
            self.options = options
            self._runtime = runtime
            self.name = options["name"]
            self.created = False

        async def start(self):
            self.created = self.name not in records
            await asyncio.sleep(0)  # Expose overlapping same-name creation.
            if self.created:
                records[self.name] = SimpleNamespace(
                    name=self.name,
                    image=self.options["image"],
                    cpus=self.options["cpus"],
                    memory_mib=self.options["memory_mib"],
                    created_at="2026-01-01T00:00:00Z",
                    state=SimpleNamespace(status="running"),
                )
            records[self.name].state.status = "running"

        def info(self):
            return records[self.name]

        async def stop(self):
            records[self.name].state.status = "stopped"

        async def exec(self, *args, **kwargs):
            return SimpleNamespace(
                exit_code=7,
                stdout="界界abc",
                stderr="seccomp not available\nreal error",
                error_message="command failed",
            )

    monkeypatch.setattr(adapter.boxlite, "Boxlite", lambda options: runtime)
    monkeypatch.setattr(adapter, "SimpleBox", Box)
    return runtime, records


@pytest.mark.asyncio
async def test_boxlite_rejects_missing_preloaded_oci_layout_before_sdk_start(
    engine, monkeypatch, tmp_path
):
    _runtime, records = engine
    monkeypatch.setenv("XAGENT_BOXLITE_ROOTFS_PATH", str(tmp_path / "missing"))
    service = adapter.BoxliteSandboxService(adapter.MemBoxliteStore())

    with pytest.raises(RuntimeError, match="OCI layout is missing"):
        await service.get_or_create("missing-image")
    assert records == {}


@pytest.mark.asyncio
async def test_isolated_guest_uses_native_advanced_security_options(engine):
    runtime, records = engine
    captured = {}
    original = adapter.SimpleBox

    def capture(**options):
        captured.update(options)
        return original(**options)

    # Only the native SDK boundary is substituted; the service still constructs
    # its actual AdvancedBoxOptions and passes it to the SimpleBox boundary.
    adapter.SimpleBox = capture
    try:
        service = adapter.BoxliteSandboxService(adapter.MemBoxliteStore())
        await service.get_or_create(
            "isolated", config=SandboxConfig(network_isolated=True)
        )
    finally:
        adapter.SimpleBox = original
    assert captured["runtime"] is runtime
    assert isinstance(captured["advanced"], adapter.boxlite.boxlite.AdvancedBoxOptions)
    assert records["isolated"].state.status == "running"


@pytest.mark.asyncio
async def test_reuse_stop_restore_and_delete_preserve_service_state(engine):
    runtime, records = engine
    store = adapter.MemBoxliteStore()
    service = adapter.BoxliteSandboxService(store)
    template = SandboxTemplate(type="image", image="test-image")
    config = SandboxConfig(cpus=1, memory=256)
    first, second = await asyncio.gather(
        service.get_or_create("task", template, config),
        service.get_or_create("task", template, config),
    )
    assert len(records) == 1
    assert (await first.info()).created_at == (await second.info()).created_at
    await first.stop()
    assert (await service.list_sandboxes())[0].state == "stopped"
    restored = await service.get_or_create("task")
    assert (await restored.info()).state == "running"
    assert (await restored.info()).template.image == "test-image"
    await service.delete("task")
    assert await service.list_sandboxes() == []
    assert store.get_info("task") is None
    await service.delete("task")


@pytest.mark.asyncio
async def test_runtime_metadata_recovers_after_store_loss_and_delete_failure(
    engine, monkeypatch
):
    runtime, records = engine
    service = adapter.BoxliteSandboxService(adapter.MemBoxliteStore())
    await service.get_or_create("survivor")
    recovered = adapter.BoxliteSandboxService(adapter.MemBoxliteStore())
    assert [info.name for info in await recovered.list_sandboxes()] == ["survivor"]
    sandbox = await recovered.get_or_create("survivor")
    assert (await sandbox.info()).template.image == records["survivor"].image
    monkeypatch.setattr(
        runtime, "remove", AsyncMock(side_effect=OSError("disk unavailable"))
    )
    with pytest.raises(RuntimeError) as error:
        await recovered.delete("survivor")
    assert isinstance(error.value.__cause__, OSError)
    assert [info.name for info in await recovered.list_sandboxes()] == ["survivor"]


@pytest.mark.asyncio
async def test_execution_preserves_failures_and_caps_utf8_after_warning_removal(engine):
    service = adapter.BoxliteSandboxService(adapter.MemBoxliteStore())
    sandbox = await service.get_or_create("execution")
    result = await sandbox.exec("command", max_output_bytes=4)
    assert result.exit_code == 7
    assert result.stdout == "界"
    assert result.stderr == "real"
    assert result.truncated is True
    assert result.error_message == "command failed"
    uncapped = await sandbox.run_code("print('hello')")
    assert uncapped.stdout == "界界abc"
    assert uncapped.stderr == "real error"
    assert uncapped.truncated is False
