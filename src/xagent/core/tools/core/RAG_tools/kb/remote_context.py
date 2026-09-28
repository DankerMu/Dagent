"""Authorization of a remote collection within the coordinator context."""

from __future__ import annotations

from typing import Any

from .models import KBAccessMode
from .ragflow_binding import (
    ReadOnlyKnowledgeBaseError,
    ragflow_binding,
    verify_binding_claim,
)


async def authorize_remote_context(
    info: Any,
    *,
    collection: str,
    access_mode: KBAccessMode,
    metadata_store: Any,
    user_id: int | None,
) -> None:
    """Require a read-only, owner-claimed publication before opening a handle."""
    binding = ragflow_binding(info)
    if access_mode is not KBAccessMode.READ:
        raise ReadOnlyKnowledgeBaseError("RAGFlow knowledge bases are read-only")
    config = await metadata_store.get_collection_config(
        collection, user_id, is_admin=False
    )
    if binding is not None:
        verify_binding_claim(binding, config, user_id)
