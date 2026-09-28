"""Backend publication, resolution and capabilities for KB collections."""

from __future__ import annotations

from typing import Any

from ..core.schemas import CollectionInfo
from .async_utils import maybe_await
from .models import KBBackendCapabilities, KBStorageBackend

KB_STORAGE_METADATA_KEY = "kb_storage"


async def bind_native_collection(info: CollectionInfo, store: Any) -> CollectionInfo:
    """Add a native marker without replacing an existing backend publication."""
    extra_metadata = dict(info.extra_metadata or {})
    if extra_metadata.get(KB_STORAGE_METADATA_KEY) is not None:
        return info
    extra_metadata[KB_STORAGE_METADATA_KEY] = {
        "backend": KBStorageBackend.LANCEDB.value
    }
    updated = info.model_copy(update={"extra_metadata": extra_metadata})
    await maybe_await(store.save_collection(updated))
    return updated


def parse_backend(raw_backend: str) -> KBStorageBackend:
    try:
        return KBStorageBackend(raw_backend.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(backend.value for backend in KBStorageBackend)
        raise ValueError(
            f"Invalid {KB_STORAGE_METADATA_KEY} backend {raw_backend!r}; "
            f"choose one of: {allowed}"
        ) from exc


def resolve_backend(info: object | None) -> KBStorageBackend:
    if info is None:
        return KBStorageBackend.LANCEDB
    extra_metadata = getattr(info, "extra_metadata", None) or {}
    binding = extra_metadata.get(KB_STORAGE_METADATA_KEY)
    if binding is None:
        return KBStorageBackend.LANCEDB
    if isinstance(binding, str):
        return parse_backend(binding)
    if isinstance(binding, dict):
        raw_backend = binding.get("backend")
        if raw_backend is None or str(raw_backend).strip() == "":
            return KBStorageBackend.LANCEDB
        return parse_backend(str(raw_backend))
    raise ValueError(
        f"Invalid {KB_STORAGE_METADATA_KEY} binding shape: {type(binding).__name__}"
    )


def capabilities_for_backend(backend: KBStorageBackend) -> KBBackendCapabilities:
    if backend is KBStorageBackend.LANCEDB:
        return KBBackendCapabilities.lancedb()
    if backend is KBStorageBackend.RAGFLOW:
        return KBBackendCapabilities.ragflow()
    return KBBackendCapabilities.unsupported()
