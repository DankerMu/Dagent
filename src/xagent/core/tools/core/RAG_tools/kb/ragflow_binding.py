"""Local, owner-scoped RAGFlow binding and read-only capability boundary."""

from __future__ import annotations

import json
from typing import Any, cast

from ......config import get_ragflow_url
from ..core.schemas import CollectionInfo, ListCollectionsResult
from ..storage.factory import get_metadata_store
from .async_utils import maybe_await


class ReadOnlyKnowledgeBaseError(ValueError):
    """A local write was requested for a remote, read-only collection."""


def ragflow_binding(info: CollectionInfo | None) -> dict[str, Any] | None:
    storage = (info.extra_metadata or {}).get("kb_storage") if info else None
    if not isinstance(storage, dict) or storage.get("backend") != "ragflow":
        return None
    dataset_id = storage.get("dataset_id")
    rerank_id = storage.get("rerank_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("Invalid RAGFlow collection binding")
    if rerank_id is not None and (
        not isinstance(rerank_id, str) or not rerank_id.strip()
    ):
        raise ValueError("Invalid RAGFlow collection reranker binding")
    return storage


async def _get_collection_or_none(name: str) -> CollectionInfo | None:
    try:
        return cast(
            CollectionInfo, await maybe_await(get_metadata_store().get_collection(name))
        )
    except ValueError as exc:
        if str(exc) in {
            f"Collection '{name}' not found",
            "Table 'collection_metadata' was not found",
        }:
            return None
        raise


def verify_binding_claim(
    binding: dict[str, Any], config: str | None, user_id: int | None
) -> None:
    """Bind authority to this publication, not merely a colliding KB name."""
    server_url = binding.get("server_url")
    try:
        record = json.loads(config or "{}")
    except (TypeError, ValueError):
        record = {}
    claim = record.get("_ragflow_binding") if isinstance(record, dict) else None
    expected = {
        "binding_id": binding.get("binding_id"),
        "dataset_id": binding["dataset_id"],
        "owner_user_id": user_id,
        "server_url": server_url,
    }
    if (
        user_id is None
        or binding.get("owner_user_id") != user_id
        or not server_url
        or not expected["binding_id"]
        or claim != expected
    ):
        raise PermissionError("Knowledge base access denied")


def assert_binding_server(binding: dict[str, Any]) -> None:
    """Do not silently retarget a publication when the deployment URL changes."""
    from ..ragflow.client import RagflowError

    try:
        current_url = get_ragflow_url()
    except ValueError:
        raise RagflowError("Invalid RAGFlow connection configuration") from None
    if not current_url or binding.get("server_url") != current_url:
        raise RagflowError(
            "RAGFlow server changed; disconnect and rebind this knowledge base"
        )


async def is_visible_ragflow_collection(
    info: CollectionInfo, *, user_id: int | None
) -> bool:
    """Filter publication collisions without hiding storage failures."""
    binding = ragflow_binding(info)
    if binding is None:
        return True
    config = await maybe_await(
        get_metadata_store().get_collection_config(info.name, user_id, is_admin=False)
    )
    try:
        verify_binding_claim(binding, config, user_id)
    except PermissionError:
        return False
    return True


async def filter_visible_collections(
    result: ListCollectionsResult, *, user_id: int | None
) -> ListCollectionsResult:
    """Apply publication ownership while preserving native listing identity."""
    visible = [
        info
        for info in result.collections
        if await is_visible_ragflow_collection(info, user_id=user_id)
    ]
    if len(visible) == len(result.collections):
        return result
    return result.model_copy(
        update={"collections": visible, "total_count": len(visible)}
    )


async def get_visible_ragflow_binding(
    name: str, *, user_id: int | None, is_admin: bool = False
) -> dict[str, Any] | None:
    """Never authorize remote access from global metadata alone.

    The config row determines tenant visibility, just as it does for ordinary
    KB listing. Team callers must pass the already ACL-resolved storage owner.
    """
    store = get_metadata_store()
    info = await _get_collection_or_none(name)
    binding = ragflow_binding(info)
    if binding is None:
        return None
    config = await maybe_await(
        store.get_collection_config(name, user_id, is_admin=False)
    )
    verify_binding_claim(binding, config, user_id)
    return binding


def get_visible_ragflow_binding_sync(
    name: str, *, user_id: int | None, is_admin: bool = False
) -> dict[str, Any] | None:
    from .coordinator import _run_in_separate_loop

    return _run_in_separate_loop(
        get_visible_ragflow_binding(name, user_id=user_id, is_admin=is_admin)
    )


async def require_local_write(name: str) -> None:
    """Fail before any local ingest, rename, document or config mutation."""
    info = await _get_collection_or_none(name)
    if ragflow_binding(info) is not None:
        raise ReadOnlyKnowledgeBaseError("RAGFlow knowledge bases are read-only")


def require_local_write_sync(name: str) -> None:
    from .coordinator import _run_in_separate_loop

    _run_in_separate_loop(require_local_write(name))
