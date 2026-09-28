"""Collection-level search execution and native/remote result presentation."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)
_READONLY_WARNING_PREFIX = "READONLY_MODE:"


def is_remote_collection(info: Any) -> bool:
    storage = (getattr(info, "extra_metadata", None) or {}).get("kb_storage")
    return isinstance(storage, dict) and storage.get("backend") == "ragflow"


def has_searchable_content(info: Any) -> bool:
    """Remote readiness belongs to RAGFlow, not local embedding row counts."""
    return info.embeddings != 0 or is_remote_collection(info)


def _collection_outcome(
    result: Any, collection_info: Any
) -> tuple[list[dict[str, Any]], str | None, str | None, int]:
    """Keep result/error accounting separate from the bounded remote operation."""
    name = collection_info.name
    if result.status not in {"success", "partial_success"}:
        message = result.message or "; ".join(result.warnings) or "search failed"
        logger.warning(
            "Search pipeline returned status '%s' for '%s': %s",
            result.status,
            name,
            message,
        )
        return [], f"{name}: {message}", None, 0
    warning_text = "; ".join(
        warning
        for warning in result.warnings
        if not warning.startswith(_READONLY_WARNING_PREFIX)
    )
    warning = f"{name}: {warning_text}" if warning_text else None
    if not result.results:
        return [], None, warning, 0
    results = []
    for item in result.results:
        mapped = dict(item)
        mapped["collection"] = name
        results.append(mapped)
    documents = (
        len({item.get("doc_id") for item in results if item.get("doc_id")})
        if is_remote_collection(collection_info)
        else collection_info.documents
    )
    return results, None, warning, documents


async def search_collection(
    collection_info: Any,
    *,
    base_search_config: dict[str, Any],
    tool_args: Any,
    user_id: int | None,
    is_admin: bool,
    timeout_seconds: float,
    run_search: Callable[..., Any],
) -> tuple[list[dict[str, Any]], str | None, str | None, int]:
    """Search one collection without letting its failure abort sibling searches."""
    collection_name = "<unknown>"
    try:
        collection_name = collection_info.name
        is_remote = is_remote_collection(collection_info)
        search_config = dict(base_search_config)
        rerank = tool_args.rerank_model_id or getattr(
            collection_info, "rerank_model_id", None
        )
        if rerank and not is_remote:
            search_config["rerank_model_id"] = rerank
        storage_user_id = getattr(collection_info, "storage_user_id", None)
        logger.info(
            "Searching collection '%s' for: %s", collection_name, tool_args.query
        )
        # wait_for also covers executor queueing; a timed-out worker may continue.
        result = await asyncio.wait_for(
            asyncio.to_thread(
                run_search,
                collection=collection_name,
                query_text=tool_args.query,
                config=search_config,
                user_id=storage_user_id if storage_user_id is not None else user_id,
                is_admin=False
                if getattr(collection_info, "ownership", "personal") == "team"
                else is_admin,
            ),
            timeout=timeout_seconds,
        )
        return _collection_outcome(result, collection_info)
    except asyncio.TimeoutError:
        logger.warning(
            "Search of collection '%s' exceeded %ss", collection_name, timeout_seconds
        )
        return (
            [],
            f"{collection_name}: search timed out after {timeout_seconds}s",
            None,
            0,
        )
    except Exception as exc:
        logger.warning("Failed to search collection '%s': %s", collection_name, exc)
        return [], f"{collection_name}: {exc}", None, 0


def format_search_results(
    results: list[dict[str, Any]], query: str, total_documents: int
) -> tuple[list[dict[str, Any]], str]:
    """Present native file provenance or remote identifiers without inventing paths."""
    formatted = []
    for result in results:
        metadata = result.get("metadata") or {}
        remote = metadata.get("backend") == "ragflow"
        source_path = "" if remote else metadata.get("source", "")
        item = {
            "collection": result.get("collection", "unknown"),
            "score": result.get("score", 0.0),
            "text": result.get("text", ""),
            "document_name": metadata.get("document_keyword", "")
            if remote
            else os.path.basename(source_path)
            if source_path
            else "",
            "source_path": source_path,
            "doc_id": result.get("doc_id")
            or metadata.get("doc_id")
            or metadata.get("document_id", ""),
            "chunk_id": result.get("chunk_id") or metadata.get("chunk_id", ""),
        }
        if remote:
            item.update(
                backend="ragflow",
                score_kind=metadata.get("score_kind", "reciprocal_rank"),
                ragflow_similarity=metadata.get("ragflow_similarity"),
                dataset_id=metadata.get("dataset_id", ""),
            )
        formatted.append(item)
    summary = (
        f"Found {len(results)} relevant results from {total_documents} "
        f"documents for query: '{query}'"
    )
    return formatted, summary
