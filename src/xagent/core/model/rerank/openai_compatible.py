"""OpenAI-compatible rerank client (Jina/vLLM ``/rerank`` schema)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .base import BaseRerank

logger = logging.getLogger(__name__)


def _rerank_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    path = urlparse(cleaned).path.rstrip("/")
    if path.endswith("/rerank") or path.endswith("/reranks"):
        return cleaned
    return f"{cleaned}/rerank"


class OpenAICompatibleRerank(BaseRerank):
    """Generic OpenAI-compatible rerank client.

    Posts ``{model, query, documents, top_n}`` to ``{base_url}/rerank`` and
    reads a Jina/vLLM-style ``results`` list. An explicit ``base_url`` is
    required; public vendor endpoints are never implied.
    """

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        top_n: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        if not base_url or not str(base_url).strip():
            raise ValueError("base_url is required for OpenAI-compatible rerank")
        self.model = model
        self.api_key = api_key
        self.base_url = str(base_url).strip().rstrip("/")
        self.top_n = top_n
        self.timeout = float(timeout) if timeout is not None else 60.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_url(self) -> str:
        return _rerank_url(self.base_url)

    def _request_model(self) -> str:
        return self.model

    def _provider_label(self) -> str:
        return "OpenAI-compatible"

    def _request_results(self, documents: list[str], query: str) -> list[Any]:
        """Send one request and validate the shared compatible rerank envelope."""
        payload: dict[str, Any] = {
            "model": self._request_model(),
            "query": query,
            "documents": documents,
        }
        if self.top_n is not None:
            payload["top_n"] = self.top_n

        response = requests.post(
            self._request_url(),
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        label = self._provider_label()
        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError(
                f"{label} rerank endpoint returned non-JSON response: "
                f"{response.text[:200]!r}"
            ) from exc

        results = data.get("results")
        if not isinstance(results, list):
            raise ValueError(
                f"{label} rerank response missing 'results' list: {list(data.keys())}"
            )
        return results

    def _warn_invalid_index(self, index: int, count: int) -> None:
        """Compatible endpoints silently omit duplicate/out-of-range indices."""
        return None

    def compress_with_scores(
        self,
        documents: Sequence[str],
        query: str,
    ) -> list[tuple[str, float]]:
        documents = list(documents)
        if not documents:
            return []

        results = self._request_results(documents, query)
        ordered_pairs: list[tuple[str, float]] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                logger.warning(
                    "%s rerank: skipping non-dict result entry: %r",
                    self._provider_label(),
                    item,
                )
                continue
            try:
                index = int(item["index"])
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self._provider_label()} rerank result missing/invalid "
                    f"'index' or 'relevance_score': {item}"
                ) from exc
            if 0 <= index < len(documents) and index not in seen:
                ordered_pairs.append((documents[index], score))
                seen.add(index)
            else:
                self._warn_invalid_index(index, len(documents))

        for idx, doc in enumerate(documents):
            if idx not in seen:
                ordered_pairs.append((doc, 0.0))
        return ordered_pairs

    def compress(
        self,
        documents: Sequence[str],
        query: str,
    ) -> Sequence[str]:
        return [text for text, _score in self.compress_with_scores(documents, query)]
