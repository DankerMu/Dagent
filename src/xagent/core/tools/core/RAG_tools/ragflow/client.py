"""Read-only RAGFlow REST boundary; embedding stays with the remote dataset."""

from __future__ import annotations

import re
from typing import Any, cast

import httpx

from ......config import (
    get_ragflow_api_key,
    get_ragflow_timeout_seconds,
    get_ragflow_url,
)


class RagflowError(RuntimeError):
    """Safe, credential-free remote knowledge service failure."""


class RagflowClient:
    """Use a deployment-controlled endpoint, never a model-supplied URL."""

    def __init__(self) -> None:
        try:
            self._url = get_ragflow_url()
            self._key = get_ragflow_api_key()
            self._timeout = get_ragflow_timeout_seconds()
        except ValueError:
            raise RagflowError("Invalid RAGFlow connection configuration") from None
        if not self._url or not self._key:
            raise RagflowError(
                "Configure XAGENT_RAGFLOW_URL and XAGENT_RAGFLOW_API_KEY"
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            # Do not forward a LAN credential through ambient proxies or redirects.
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=self._timeout
            ) as client:
                response = client.request(
                    method,
                    f"{self._url}/api/v1{path}",
                    headers={"Authorization": f"Bearer {self._key}"},
                    **kwargs,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError):
            raise RagflowError(
                "RAGFlow request failed; check connectivity and credentials"
            ) from None
        if not isinstance(body, dict) or body.get("code") != 0:
            # Upstream messages can contain credential-bearing provider errors.
            raise RagflowError(
                "RAGFlow rejected the request; check dataset and model configuration"
            )
        return body

    @staticmethod
    def _dataset_id(dataset_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", dataset_id):
            raise RagflowError("Invalid RAGFlow dataset identifier")
        return dataset_id

    def list_datasets(self) -> list[dict[str, Any]]:
        datasets: list[dict[str, Any]] = []
        seen: set[str] = set()
        page = 1
        while True:
            body = self._request(
                "GET", "/datasets", params={"page": page, "page_size": 100}
            )
            batch = body.get("data")
            if not isinstance(batch, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("name"), str)
                for item in batch
            ):
                raise RagflowError("Invalid RAGFlow dataset response")
            if any(item["id"] in seen for item in batch):
                raise RagflowError(
                    "RAGFlow dataset pagination changed; refresh the list"
                )
            datasets.extend(batch)
            seen.update(item["id"] for item in batch)
            if len(batch) < 100:
                return datasets
            page += 1

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        dataset_id = self._dataset_id(dataset_id)
        data = self._request("GET", f"/datasets/{dataset_id}").get("data")
        if not isinstance(data, dict) or data.get("id") != dataset_id:
            raise RagflowError("Invalid RAGFlow dataset response")
        return data

    def retrieve(
        self,
        dataset_id: str,
        question: str,
        *,
        top_k: int = 5,
        rerank_id: str | None = None,
    ) -> list[dict[str, Any]]:
        dataset_id = self._dataset_id(dataset_id)
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        payload: dict[str, Any] = {
            "dataset_ids": [dataset_id],
            "question": question,
            "page_size": top_k,
            "page": 1,
        }
        if rerank_id:
            payload["rerank_id"] = rerank_id
        data = self._request("POST", "/retrieval", json=payload).get("data")
        if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
            raise RagflowError("Invalid RAGFlow retrieval response")
        chunks = data["chunks"]
        for chunk in chunks:
            if (
                not isinstance(chunk, dict)
                or any(
                    not isinstance(chunk.get(field), str)
                    for field in ("id", "content", "document_id", "document_keyword")
                )
                or chunk.get("dataset_id") != dataset_id
            ):
                raise RagflowError("Invalid or out-of-scope RAGFlow retrieval result")
        return cast(list[dict[str, Any]], chunks[:top_k])
