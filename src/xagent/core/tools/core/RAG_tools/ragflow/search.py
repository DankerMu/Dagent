"""RAGFlow retrieval mapped into the shared search result contract."""

from __future__ import annotations

from typing import Any

from ..core.schemas import SearchConfig, SearchPipelineResult, SearchResult, SearchType
from ..kb.ragflow_binding import assert_binding_server
from .client import RagflowClient


def search_ragflow(
    binding: dict[str, Any], query_text: str, cfg: SearchConfig
) -> SearchPipelineResult:
    """Retrieve remote chunks without loading local embedding or rerank models."""
    if cfg.filters:
        raise ValueError("RAGFlow search does not support local retrieval filters")
    if cfg.search_type is not SearchType.HYBRID:
        raise ValueError("RAGFlow search does not support the requested search type")
    assert_binding_server(binding)
    chunks = RagflowClient().retrieve(
        binding["dataset_id"],
        query_text,
        top_k=cfg.top_k,
        rerank_id=binding["rerank_id"],
    )
    results = []
    for rank, chunk in enumerate(chunks):
        metadata = {
            "backend": "ragflow",
            "dataset_id": chunk["dataset_id"],
            "document_id": chunk["document_id"],
            "chunk_id": chunk["id"],
            "document_keyword": chunk.get("document_keyword", ""),
            "ragflow_similarity": chunk.get("similarity"),
            "score_kind": "reciprocal_rank",
        }
        results.append(
            SearchResult(
                doc_id=chunk["document_id"],
                chunk_id=chunk["id"],
                text=chunk["content"],
                score=1.0 / (rank + 1),
                parse_hash="",
                model_tag="",
                metadata=metadata,
            )
        )
    return SearchPipelineResult(
        status="success",
        search_type=SearchType.HYBRID,
        results=results,
        result_count=len(results),
        message="Search completed successfully",
        used_rerank=binding["rerank_id"] is not None,
    )
