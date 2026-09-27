"""Docker sandbox image availability without runtime registry access."""

import asyncio
from typing import Any

from docker.errors import ImageNotFound

from ..core.offline_assets import sandbox_image_missing_error


async def ensure_image(client: Any, image: str) -> None:
    """Require an image prepared on this host before creating a container.

    Build/preload may pull or load it on a connected host; deployed runtime may not.
    """
    try:
        await asyncio.to_thread(client.images.get, image)
    except ImageNotFound as exc:
        raise sandbox_image_missing_error(image) from exc
