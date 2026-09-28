"""Document search pipeline orchestrating multiple retrieval strategies."""

from __future__ import annotations

import logging
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import requests

from xagent.core.model.embedding.base import BaseEmbedding
from xagent.core.model.rerank.openai_compatible import OpenAICompatibleRerank
from xagent.core.model.rerank.xinference import XinferenceRerank

from ..core.exceptions import (
    DocumentValidationError,
    RagCoreException,
    VectorValidationError,
)
from ..core.schemas import (
    DenseSearchResponse,
    HybridSearchResponse,
    SearchConfig,
    SearchPipelineResult,
    SearchResult,
    SearchType,
    SparseSearchResponse,
)
from ..kb.ragflow_binding import get_visible_ragflow_binding_sync
from ..progress import ProgressManager, ProgressTracker
from ..ragflow.search import search_ragflow
from ..retrieval.search_dense import search_dense
from ..retrieval.search_hybrid import search_hybrid
from ..retrieval.search_sparse import search_sparse
from ..utils.config_utils import coerce_search_config
from ..utils.embedding_utils import normalize_single_embedding
from ..utils.model_resolver import resolve_embedding_adapter, resolve_rerank_adapter
from ..utils.user_scope import resolve_user_scope

if TYPE_CHECKING:
    from ..kb import KBPipelineCompatibilityFacade

logger = logging.getLogger(__name__)


def _extract_openai_compatible_rerank(
    rerank_adapter: Any,
) -> Optional[OpenAICompatibleRerank]:
    """Extract a configured OpenAI-compatible rerank client."""
    if isinstance(rerank_adapter, OpenAICompatibleRerank):
        return rerank_adapter
    wrapped = getattr(rerank_adapter, "_rerank_model", None)
    return wrapped if isinstance(wrapped, OpenAICompatibleRerank) else None


def _extract_xinference_rerank(
    rerank_adapter: Any,
) -> Optional[XinferenceRerank]:
    """Extract XinferenceRerank instance from rerank adapter.

    Args:
        rerank_adapter: Rerank adapter instance (may be wrapped).

    Returns:
        XinferenceRerank instance if found, None otherwise.
    """
    if isinstance(rerank_adapter, XinferenceRerank):
        return rerank_adapter
    if hasattr(rerank_adapter, "_rerank_model") and isinstance(
        rerank_adapter._rerank_model, XinferenceRerank
    ):
        return rerank_adapter._rerank_model
    return None


def _resolve_unified_rerank(
    cfg: Optional[SearchConfig] = None,
) -> Optional[Union[OpenAICompatibleRerank, XinferenceRerank]]:
    """Resolve the configured OpenAI-compatible or Xinference reranker."""

    # If no rerank_model_id is provided at all, don't even try
    # This ensures "no KB binding = no rerank" contract is respected
    model_id = cfg.rerank_model_id if cfg and cfg.rerank_model_id else None
    if not model_id:
        return None

    # Try unified resolver: explicit model_id > hub > env fallback
    try:
        rerank_cfg, rerank_adapter = resolve_rerank_adapter(
            model_id=model_id,
            api_key=None,
            base_url=None,
            timeout_sec=None,
        )
        compatible_rerank = _extract_openai_compatible_rerank(rerank_adapter)
        if compatible_rerank:
            return compatible_rerank
        xinference_rerank = _extract_xinference_rerank(rerank_adapter)
        if xinference_rerank:
            return xinference_rerank
    except (RagCoreException, ValueError, TypeError, ImportError) as exc:
        logger.warning(
            "Failed to load rerank adapter from unified resolver: %s",
            exc,
        )

    return None


def _try_unified_rerank(
    results: List[SearchResult],
    query_text: str,
    cfg: SearchConfig,
    warnings: List[str],
) -> Optional[Tuple[List[SearchResult], bool, List[str]]]:
    """Rerank results with the configured LAN-compatible provider."""
    rerank_model = _resolve_unified_rerank(cfg)

    if rerank_model is None:
        return None

    documents = [result.text for result in results]
    if not documents:
        return None

    try:
        # Both compatible and Xinference clients return scored document pairs.
        reranked_pairs = rerank_model.compress_with_scores(documents, query_text)
        ordered_results = _map_reranked_pairs_to_results(reranked_pairs, results)

        if not ordered_results:
            provider_name = type(rerank_model).__name__
            warnings.append(
                f"{provider_name} rerank returned no recognizable documents."
            )
            return None

        # After rerank we always truncate to the user-requested top_k. The
        # earlier larger fetch_top_k is the *candidate pool* for rerank to
        # work on; the final response size is cfg.top_k.
        ordered_results = _apply_rerank_top_k_limit(ordered_results, cfg.top_k)
        return ordered_results, True, warnings

    except (
        requests.exceptions.RequestException,
        requests.exceptions.HTTPError,
        KeyError,
        ValueError,
        TypeError,
    ) as exc:
        provider_name = type(rerank_model).__name__
        logger.warning("%s rerank failed: %s", provider_name, exc)
        warnings.append(f"{provider_name} rerank failed: {exc}")
        return None


def _encode_query_vector(adapter: BaseEmbedding, query_text: str) -> List[float]:
    """Encode query text into a single vector using embedding adapter.

    Provider responses come in many shapes (``List[float]``, ``List[List[float]]``,
    ``List[dict]`` with an ``"embedding"`` key, or even non-list types such as
    ``numpy.ndarray``). Delegate to ``normalize_single_embedding`` so the
    search path behaves consistently with the ingestion path, which already
    uses the same helper.

    Raises:
        VectorValidationError: If encoding fails or the provider response
            cannot be normalized to a single numeric vector.
    """
    try:
        raw_vector = adapter.encode(query_text)
    except Exception as exc:  # noqa: BLE001
        raise VectorValidationError(
            f"Embedding adapter failed to encode query: {exc}"
        ) from exc

    return normalize_single_embedding(raw_vector)


def _serialize_warnings(warnings: Sequence) -> List[str]:
    """Convert warning objects to human-readable strings."""

    serialized: List[str] = []
    for warning in warnings:
        code = getattr(warning, "code", None)
        message = getattr(warning, "message", "")
        if code:
            serialized.append(f"{code}: {message}")
        else:
            serialized.append(str(message))
    return serialized


def _map_reranked_texts_to_results(
    reranked_texts: Sequence[str], original_results: List[SearchResult]
) -> List[SearchResult]:
    """Map reranked texts back to SearchResult objects preserving order.

    Args:
        reranked_texts: List of texts in reranked order
        original_results: Original search results

    Returns:
        List of SearchResult objects in reranked order
    """
    # Build mapping from text to list of results (handles duplicates)
    text_to_results: Dict[str, List[SearchResult]] = {}
    for result in original_results:
        text_to_results.setdefault(result.text, []).append(result)

    # Build ordered results list from reranked texts
    ordered_results: List[SearchResult] = []
    for text in reranked_texts:
        queue = text_to_results.get(text)
        if queue:
            ordered_results.append(queue.pop(0))

    # Append any remaining results preserving original order
    for queue in text_to_results.values():
        ordered_results.extend(queue)

    return ordered_results


def _map_reranked_pairs_to_results(
    reranked_pairs: Sequence[tuple[str, float]],
    original_results: List[SearchResult],
) -> List[SearchResult]:
    """Map (text, rerank_score) pairs back to SearchResult, overwriting score.

    The SearchResult.score field has a [0, 1] constraint and is the value
    the UI/API exposes as ``评分``. We want this to reflect the rerank
    model's relevance score after reranking, not the original embedding /
    RRF score. Returns SearchResult copies with ``score`` replaced by the
    rerank relevance score (clamped to [0, 1]).

    Documents not returned by the reranker get score=0.0 so they appear
    at the bottom while respecting the [0.0, 1.0] score contract.
    """
    text_to_results: Dict[str, List[SearchResult]] = {}
    for result in original_results:
        text_to_results.setdefault(result.text, []).append(result)

    ordered_results: List[SearchResult] = []
    for text, raw_score in reranked_pairs:
        queue = text_to_results.get(text)
        if not queue:
            continue
        original = queue.pop(0)
        clamped = max(0.0, min(1.0, float(raw_score)))
        ordered_results.append(original.model_copy(update={"score": clamped}))

    # Append any remaining (un-reranked) results at the bottom with
    # score=0.0.  The list order already puts them after the reranked
    # items, so they naturally appear at the bottom without needing to
    # violate the [0.0, 1.0] score contract with negative values.
    for queue in text_to_results.values():
        for unreranked in queue:
            ordered_results.append(unreranked.model_copy(update={"score": 0.0}))

    return ordered_results


def _apply_rerank_top_k_limit(
    results: List[SearchResult], rerank_top_k: Optional[int]
) -> List[SearchResult]:
    """Apply rerank_top_k limit if specified.

    Args:
        results: Results to limit
        rerank_top_k: Optional limit (None or <= 0 means no limit)

    Returns:
        Limited results list
    """
    if rerank_top_k is not None and rerank_top_k > 0:
        return results[:rerank_top_k]
    return results


def _apply_rerank_if_needed(
    results: List[SearchResult],
    query_text: str,
    cfg: SearchConfig,
) -> Tuple[List[SearchResult], bool, List[str]]:
    """Optionally rerank using the KB's configured compatible or Xinference model."""

    warnings: List[str] = []
    if not results:
        logger.debug("Skipping rerank: No search results to rerank")
        return results, False, warnings

    # Rerank only when the knowledge base has an explicit model binding.
    unified_result = _try_unified_rerank(results, query_text, cfg, warnings)
    if unified_result:
        rerank_model = _resolve_unified_rerank(cfg)
        provider_name = type(rerank_model).__name__ if rerank_model else "Unified"
        logger.info("Successfully applied %s rerank", provider_name)
        return unified_result

    # No rerank model (or it failed): the fused order is already correct, pass it through.
    return results, False, warnings


def _limit_results(
    results: List[SearchResult], cfg: SearchConfig
) -> List[SearchResult]:
    """Limit results according to top_k configuration."""

    final_limit = cfg.top_k
    if final_limit <= 0:
        return results
    return results[:final_limit]


def _build_pipeline_result(
    *,
    status: str,
    search_type: SearchType,
    results: List[SearchResult],
    warnings: List[str],
    message: str,
    used_rerank: bool,
    cfg: SearchConfig,
) -> SearchPipelineResult:
    """Build pipeline response object."""

    limited_results = _limit_results(results, cfg)
    return SearchPipelineResult(
        status=status,
        search_type=search_type,
        results=limited_results,
        result_count=len(limited_results),
        warnings=warnings,
        message=message,
        used_rerank=used_rerank,
    )


def _execute_sparse_search(
    collection: str,
    query_text: str,
    cfg: SearchConfig,
    model_tag: str,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> Tuple[List[SearchResult], str, List[str], str]:
    """Execute sparse search and return components for pipeline result."""

    fetch_top_k = max(cfg.top_k, cfg.rerank_top_k or 0)
    sparse_response: SparseSearchResponse = search_sparse(
        collection=collection,
        model_tag=model_tag,
        query_text=query_text,
        top_k=fetch_top_k or cfg.top_k,
        filters=cfg.filters,
        readonly=cfg.readonly,
        user_id=user_id,
        is_admin=is_admin,
    )
    warnings = _serialize_warnings(sparse_response.warnings)
    status = sparse_response.status or "success"
    message = (
        "Sparse search completed successfully"
        if sparse_response.status == "success"
        else "Sparse search completed with warnings"
    )
    return list(sparse_response.results), status, warnings, message


SearchConfigInput = Union[SearchConfig, Mapping[str, Any]]


def _get_pipeline_compatibility_facade() -> "KBPipelineCompatibilityFacade":
    """Return the coordinator-owned pipeline compatibility facade."""
    from ..kb import get_kb_coordinator

    return get_kb_coordinator().pipeline_compatibility


def _handle_search_error(
    exc: Exception,
    current_step: str,
    search_type: SearchType,
    warnings: List[str],
) -> SearchPipelineResult:
    """Unify error handling for the search pipeline."""
    logger.exception(
        "Document search pipeline failed at step '%s': %s", current_step, exc
    )
    return SearchPipelineResult(
        status="error",
        search_type=search_type,
        results=[],
        result_count=0,
        warnings=warnings + [f"{current_step}: {exc}"],
        message=f"{current_step} failed: {exc}",
        used_rerank=False,
    )


def search_documents(
    collection: str,
    query_text: str,
    *,
    config: Optional[SearchConfig] = None,
    progress_manager: Optional[ProgressManager] = None,
    user_id: Optional[int] = None,
    is_admin: Optional[bool] = None,
) -> SearchPipelineResult:
    """Execute the document search pipeline end-to-end."""
    return _get_pipeline_compatibility_facade().search_documents(
        collection=collection,
        query_text=query_text,
        config=config,
        progress_manager=progress_manager,
        user_id=user_id,
        is_admin=is_admin,
    )


def _prepare_search_request(
    collection: str,
    query_text: str,
    config: Optional[SearchConfig],
    user_id: Optional[int],
    is_admin: Optional[bool],
) -> tuple[SearchConfig, Optional[int], bool]:
    """Resolve caller scope and validate a request before accessing either backend."""
    scope = resolve_user_scope(user_id=user_id, is_admin=is_admin)
    cfg = (
        config
        if isinstance(config, SearchConfig)
        else coerce_search_config(config or {})
    )
    if not collection or not isinstance(collection, str):
        raise DocumentValidationError("collection must be a non-empty string")
    if not query_text or not isinstance(query_text, str):
        raise DocumentValidationError("query_text must be a non-empty string")
    return cfg, scope.user_id, scope.is_admin


def _search_documents_impl(
    collection: str,
    query_text: str,
    *,
    config: Optional[SearchConfig] = None,
    progress_manager: Optional[ProgressManager] = None,
    user_id: Optional[int] = None,
    is_admin: Optional[bool] = None,
) -> SearchPipelineResult:
    """Execute the document search pipeline end-to-end.

    The pipeline coordinates sparse, dense, or hybrid retrieval strategies,
    applies optional reranking, and consolidates warnings plus status for
    downstream consumers. This is the canonical entry point for REST
    endpoints and LangGraph tools.

    Args:
        collection: Logical collection to search within; must correspond to an
            existing chunk/embedding dataset with bound embedding model.
        query_text: Natural-language query or keyword phrase issued by the caller.
        config: Optional search configuration override. When provided, embedding_model_id
            will be overridden by collection's bound model if available.
        progress_manager: Optional progress manager for tracking.
        user_id: Optional user ID for ownership tracking.
        is_admin: Optional admin override; when omitted, falls back to request scope.

    Returns:
        SearchPipelineResult: Structured result containing status, selected search
        type (sparse/dense/hybrid), truncated results per ``top_k``, and warnings.

    Raises:
        DocumentValidationError: Missing/malformed inputs.
        EmbeddingAdapterError: Embedding model cannot be loaded.
        VectorValidationError: Query embedding fails and fallback is disabled.
    """

    cfg, user_id, is_admin = _prepare_search_request(
        collection, query_text, config, user_id, is_admin
    )
    binding = get_visible_ragflow_binding_sync(
        collection, user_id=user_id, is_admin=is_admin
    )
    if binding is not None:
        return search_ragflow(binding, query_text, cfg)

    if progress_manager is None:
        from ..progress import get_progress_manager as _get_pm

        progress_manager = _get_pm()

    requested_type = cfg.search_type
    # When a rerank model is configured we need a larger candidate pool than
    # the final top_k so the rerank step has meaningful re-ordering work to
    # do. Rule: candidate_pool = max(top_k * 4, 20), capped to a reasonable
    # ceiling. The rerank stage then truncates back to cfg.top_k.
    rerank_enabled = bool(cfg.rerank_model_id)
    if rerank_enabled:
        candidate_pool = max(cfg.top_k * 4, 20)
        candidate_pool = min(candidate_pool, 100)
        fetch_top_k = max(cfg.top_k, cfg.rerank_top_k or 0, candidate_pool)
    else:
        fetch_top_k = max(cfg.top_k, cfg.rerank_top_k or 0)
    warnings: List[str] = []

    # Get collection's bound embedding model
    from ..management.collection_manager import resolve_effective_embedding_model_sync

    try:
        model_id = resolve_effective_embedding_model_sync(
            collection, cfg.embedding_model_id
        )
        cfg = cfg.model_copy(update={"embedding_model_id": model_id})
        logger.info(
            "Using resolved embedding model '%s' for collection '%s'",
            model_id,
            collection,
        )
    except ValueError as e:
        if "not found" in str(e):
            raise DocumentValidationError(f"Collection '{collection}' not found")
        raise

    current_step = "initialize"
    task_id = f"search_{collection}_{hash(query_text) % 10000:04d}"
    progress_tracker = ProgressTracker(progress_manager, task_id)
    progress_manager.create_task(
        task_type="search",
        task_id=task_id,
        user_id=user_id,
        metadata={
            "collection": collection,
            "query": query_text[:100],
        },
    )

    try:
        embedding_config, embedding_adapter = resolve_embedding_adapter(
            cfg.embedding_model_id,
            api_key=None,
            base_url=None,
            timeout_sec=None,
        )
        # IMPORTANT: We use the Hub model ID as the single source of truth.
        # It is used for embedding table naming and persisted collection binding.
        embedding_model_id = (cfg.embedding_model_id or "").strip()
        current_step = "post_resolve_embedding"
        actual_type = requested_type
        results: List[SearchResult] = []
        status = "success"
        message = "Search completed successfully"

        if requested_type == SearchType.SPARSE:
            with progress_tracker.track_step("sparse_search"):
                pass
            current_step = "search_sparse"
            results, status, sparse_warnings, message = _execute_sparse_search(
                collection, query_text, cfg, embedding_model_id, user_id, is_admin
            )
            warnings.extend(sparse_warnings)
        else:
            # Use embedding adapter for dense/hybrid paths
            try:
                with progress_tracker.track_step("encode_query"):
                    pass
                current_step = "encode_query_vector"
                query_vector = _encode_query_vector(embedding_adapter, query_text)
            except VectorValidationError:
                if requested_type == SearchType.HYBRID and cfg.fallback_to_sparse:
                    current_step = "search_sparse_fallback"
                    logger.warning(
                        "Hybrid search embedding failed; falling back to sparse search."
                    )
                    warnings.append(
                        "Hybrid search embedding failed; fallback to sparse."
                    )
                    results, status, sparse_warnings, message = _execute_sparse_search(
                        collection,
                        query_text,
                        cfg,
                        embedding_model_id,
                        user_id,
                        is_admin,
                    )
                    warnings.extend(sparse_warnings)
                    actual_type = SearchType.SPARSE
                else:
                    raise
            else:
                if requested_type == SearchType.DENSE:
                    with progress_tracker.track_step("dense_search"):
                        pass
                    dense_response: DenseSearchResponse = search_dense(
                        collection=collection,
                        model_tag=embedding_model_id,
                        query_vector=query_vector,
                        top_k=fetch_top_k,
                        filters=cfg.filters,
                        readonly=cfg.readonly,
                        nprobes=cfg.nprobes,
                        refine_factor=cfg.refine_factor,
                        user_id=user_id,
                        is_admin=is_admin,
                    )
                    warnings.extend(_serialize_warnings(dense_response.warnings))
                    results = list(dense_response.results)
                    status = dense_response.status or "success"
                    advice = dense_response.index_advice
                    message = (
                        advice if advice else "Dense search completed successfully"
                    )
                else:  # HYBRID
                    try:
                        with progress_tracker.track_step("hybrid_search"):
                            pass
                        hybrid_response: HybridSearchResponse = search_hybrid(
                            collection=collection,
                            model_tag=embedding_model_id,
                            query_text=query_text,
                            query_vector=query_vector,
                            top_k=fetch_top_k,
                            filters=cfg.filters,
                            fusion_config=cfg.fusion_config,
                            readonly=cfg.readonly,
                            nprobes=cfg.nprobes,
                            refine_factor=cfg.refine_factor,
                            user_id=user_id,
                            is_admin=is_admin,
                        )
                    except (RagCoreException, ValueError, TypeError) as exc:
                        if cfg.fallback_to_sparse:
                            logger.warning(
                                "Hybrid search failed (%s); falling back to sparse search",
                                exc,
                            )
                            warnings.append(
                                f"Hybrid search failed and fell back to sparse: {exc}"
                            )
                            results, status, sparse_warnings, message = (
                                _execute_sparse_search(
                                    collection,
                                    query_text,
                                    cfg,
                                    embedding_model_id,
                                    user_id,
                                    is_admin,
                                )
                            )
                            warnings.extend(sparse_warnings)
                            actual_type = SearchType.SPARSE
                        else:
                            current_step = "search_hybrid"
                            raise
                    else:
                        warnings.extend(_serialize_warnings(hybrid_response.warnings))
                        results = list(hybrid_response.results)
                        status = hybrid_response.status or "success"
                        message = (
                            "Hybrid search completed successfully"
                            if status == "success"
                            else "Hybrid search completed with warnings"
                        )

        # Apply optional rerank
        current_step = "apply_rerank"
        results, used_rerank, rerank_warnings = _apply_rerank_if_needed(
            results, query_text, cfg
        )
        warnings.extend(rerank_warnings)

        return _build_pipeline_result(
            status=status,
            search_type=actual_type,
            results=results,
            warnings=warnings,
            message=message,
            used_rerank=used_rerank,
            cfg=cfg,
        )

    except (RagCoreException, Exception) as exc:
        return _handle_search_error(
            exc=exc,
            current_step=current_step,
            search_type=cfg.search_type,
            warnings=warnings,
        )


def run_document_search(
    collection: str,
    query_text: str,
    *,
    config: Optional[SearchConfigInput] = None,
    progress_manager: Optional[ProgressManager] = None,
    user_id: Optional[int] = None,
    is_admin: Optional[bool] = None,
) -> SearchPipelineResult:
    """Public entrypoint for LangGraph-compatible tooling."""
    return _get_pipeline_compatibility_facade().run_document_search(
        collection=collection,
        query_text=query_text,
        config=config,
        progress_manager=progress_manager,
        user_id=user_id,
        is_admin=is_admin,
    )


def _run_document_search_impl(
    collection: str,
    query_text: str,
    *,
    config: Optional[SearchConfigInput] = None,
    progress_manager: Optional[ProgressManager] = None,
    user_id: Optional[int] = None,
    is_admin: Optional[bool] = None,
) -> SearchPipelineResult:
    """Public entrypoint for LangGraph-compatible tooling.

    This helper accepts either a fully-instantiated :class:`SearchConfig` or a
    loose mapping (e.g., direct JSON from an API request), coerces it into the
    canonical model, and delegates to :func:`search_documents`.

    Args:
        collection: Target collection name.
        query_text: Query string issued by the caller.
        config: Optional search configuration instance or JSON-like mapping.
        progress_manager: Optional progress manager for tracking.
        user_id: Optional user ID for ownership tracking.
        is_admin: Optional admin override; when omitted, falls back to request scope.

    Returns:
        SearchPipelineResult: Same contract as :func:`search_documents`.
    """

    cfg = coerce_search_config(config if config is not None else {})
    return search_documents(
        collection,
        query_text,
        config=cfg,
        progress_manager=progress_manager,
        user_id=user_id,
        is_admin=is_admin,
    )
