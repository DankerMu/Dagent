"""Security and model independence at the remote HTTP boundary."""

import httpx
import pytest

from xagent.core.tools.core.RAG_tools.ragflow.client import RagflowClient, RagflowError


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("XAGENT_RAGFLOW_URL", "http://ragflow.test")
    monkeypatch.setenv("XAGENT_RAGFLOW_API_KEY", "private-service-key")
    original = httpx.Client

    def install(handler):
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
        )
        return RagflowClient()

    return install


def test_remote_dataset_controls_embedding_and_reranker_is_replaceable(remote):
    requests = []

    def handler(request):
        import json

        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "chunks": [
                        {
                            "id": "chunk",
                            "content": "evidence",
                            "document_id": "document",
                            "document_keyword": "source.txt",
                            "dataset_id": "dataset",
                            "similarity": -2.5,
                        }
                    ]
                },
            },
        )

    client = remote(handler)
    for model in (None, "local-bge-reranker", "another-provider/arbitrary-model"):
        result = client.retrieve("dataset", "question", rerank_id=model)
        assert result[0]["similarity"] == -2.5
    assert "rerank_id" not in requests[0]
    assert [p.get("rerank_id") for p in requests[1:]] == [
        "local-bge-reranker",
        "another-provider/arbitrary-model",
    ]
    assert all(
        set(p) <= {"dataset_ids", "question", "page_size", "page", "rerank_id"}
        for p in requests
    )


def test_cross_dataset_response_is_rejected(remote):
    client = remote(
        lambda request: httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "chunks": [
                        {
                            "id": "chunk",
                            "content": "secret",
                            "document_id": "doc",
                            "document_keyword": "file",
                            "dataset_id": "unauthorized",
                        }
                    ]
                },
            },
        )
    )
    with pytest.raises(RagflowError, match="out-of-scope"):
        client.retrieve("authorized", "question")


def test_upstream_error_cannot_expose_credentials(remote):
    client = remote(
        lambda request: httpx.Response(
            200,
            json={"code": 102, "message": "provider failed with private-service-key"},
        )
    )
    with pytest.raises(RagflowError) as exc:
        client.retrieve("dataset", "question")
    assert "private-service-key" not in str(exc.value)
    assert "rejected" in str(exc.value)


def test_redirect_does_not_forward_service_credential(remote):
    destinations = []

    def handler(request):
        destinations.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://untrusted.test/steal"})

    client = remote(handler)
    with pytest.raises(RagflowError):
        client.list_datasets()
    assert destinations == ["http://ragflow.test/api/v1/datasets?page=1&page_size=100"]


def test_dataset_listing_reads_past_first_page(remote):
    def handler(request):
        page = int(request.url.params["page"])
        records = (
            [{"id": str(i), "name": str(i)} for i in range(100)]
            if page == 1
            else [{"id": "last", "name": "Last"}]
        )
        return httpx.Response(200, json={"code": 0, "data": records})

    assert remote(handler).list_datasets()[-1] == {"id": "last", "name": "Last"}
