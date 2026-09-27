"""Xinference rerank over its OpenAI-compatible ``/v1/rerank`` HTTP endpoint."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any, Dict, List, Optional

from .openai_compatible import OpenAICompatibleRerank

logger = logging.getLogger(__name__)


class XinferenceRerank(OpenAICompatibleRerank):
    """Xinference rerank model, with Xinference discovery and endpoint policy."""

    def __init__(
        self,
        model: str,
        model_uid: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        top_n: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        self._model_uid = model_uid or model
        super().__init__(
            model=model,
            api_key=api_key or os.getenv("XINFERENCE_API_KEY"),
            base_url=base_url or "http://localhost:9997",
            top_n=top_n,
            timeout=timeout,
        )

    def _request_url(self) -> str:
        return f"{self.base_url}/v1/rerank"

    def _request_model(self) -> str:
        return self._model_uid

    def _provider_label(self) -> str:
        return "Xinference"

    def _warn_invalid_index(self, index: int, count: int) -> None:
        logger.warning(
            "Xinference rerank: skipping out-of-range or duplicate "
            "index %s (n_docs=%d)",
            index,
            count,
        )

    @staticmethod
    def list_available_models(
        base_url: str, api_key: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Discover available rerank models on a configured Xinference server."""
        try:
            from xinference_client import RESTfulClient as XinferenceClient
        except ImportError:  # pragma: no cover - fallback
            try:
                from xinference.client.restful.restful_client import (
                    RESTfulClient as XinferenceClient,
                )
            except ImportError:
                logger.error(
                    "Cannot import xinference_client. Install with "
                    "`pip install xinference-client`."
                )
                return []

        client = XinferenceClient(base_url=base_url.rstrip("/"), api_key=api_key)
        try:
            models_dict = client.list_models()
        except Exception as exc:
            logger.error("Failed to fetch rerank models from Xinference: %s", exc)
            return []
        finally:
            try:
                client.close()
            except Exception:  # pragma: no cover
                pass

        result: List[Dict[str, Any]] = []
        for model_uid, model_info in (models_dict or {}).items():
            if not isinstance(model_info, dict):
                continue
            if model_info.get("model_type") != "rerank":
                continue
            result.append(
                {
                    "id": model_info.get("model_name", model_uid),
                    "model_uid": model_uid,
                    "model_type": model_info.get("model_type", ""),
                    "model_ability": model_info.get("model_ability", []),
                    "description": model_info.get("model_description", ""),
                }
            )
        return result

    def _request_results(self, documents: list[str], query: str) -> list[Any]:
        logger.debug(
            "Xinference rerank request: url=%s model=%s n_docs=%d top_n=%s",
            self._request_url(),
            self._model_uid,
            len(documents),
            self.top_n,
        )
        return super()._request_results(documents, query)

    def compress(self, documents: Sequence[str], query: str) -> Sequence[str]:
        """Rerank by index; unlike scored calls this accepts entries without scores."""
        documents = list(documents)
        if not documents:
            return []
        results = self._request_results(documents, query)
        ordered: list[str] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                logger.warning(
                    "Xinference rerank: skipping non-dict result entry: %r", item
                )
                continue
            try:
                index = int(item["index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Xinference rerank result missing/invalid 'index': {item}"
                ) from exc
            if 0 <= index < len(documents) and index not in seen:
                ordered.append(documents[index])
                seen.add(index)
            else:
                self._warn_invalid_index(index, len(documents))
        for idx, doc in enumerate(documents):
            if idx not in seen:
                ordered.append(doc)
        return ordered
