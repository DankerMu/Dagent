"""Offline sandbox preflight without requiring a live native runtime."""

from types import SimpleNamespace

import pytest
from docker.errors import ImageNotFound

from xagent.sandbox.base import SandboxConfig, SandboxTemplate
from xagent.sandbox.docker_sandbox import DockerSandboxService, MemDockerStore


@pytest.mark.asyncio
async def test_docker_sandbox_requires_preloaded_image_without_registry_pull():
    class Images:
        pull_called = False

        def get(self, image):
            raise ImageNotFound(image)

        def pull(self, image):
            self.pull_called = True
            raise AssertionError(f"runtime pull attempted for {image}")

    images = Images()
    containers = SimpleNamespace(
        list=lambda **_kwargs: [],
        create=lambda **_kwargs: pytest.fail("container created without a local image"),
    )
    client = SimpleNamespace(images=images, containers=containers, ping=lambda: True)
    service = DockerSandboxService(MemDockerStore(), client=client, namespace="test")
    with pytest.raises(RuntimeError, match="Preload it"):
        await service.create(
            "missing-image",
            SandboxTemplate(type="image", image="sandbox:local"),
            SandboxConfig(),
        )
    assert images.pull_called is False
