"""Public KB search and binding routes keep remote data behind local ACL."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from xagent.core.tools.adapters.vibe.document_search import get_knowledge_search_tool
from xagent.core.tools.core.document_search import (
    KnowledgeSearchArgs,
    ListKnowledgeBasesArgs,
    list_knowledge_bases,
    search_knowledge_base,
)
from xagent.core.tools.core.RAG_tools.core.schemas import SearchConfig
from xagent.core.tools.core.RAG_tools.kb import get_kb_coordinator
from xagent.core.tools.core.RAG_tools.kb.ragflow_binding import (
    ReadOnlyKnowledgeBaseError,
)
from xagent.core.tools.core.RAG_tools.pipelines.document_search import (
    run_document_search,
)
from xagent.core.tools.core.RAG_tools.ragflow.client import RagflowClient, RagflowError
from xagent.core.tools.core.RAG_tools.storage.factory import (
    StorageFactory,
    get_metadata_store,
)
from xagent.web.api import kb, kb_ragflow
from xagent.web.auth_dependencies import get_current_user
from xagent.web.services.knowledge_base_team_scope import (
    KnowledgeBaseAccess,
    set_knowledge_base_team_hooks,
    snapshot_knowledge_base_team_hooks,
)


@pytest.fixture
def remote_store(monkeypatch, tmp_path):
    monkeypatch.setenv("LANCEDB_DIR", str(tmp_path / "kb"))
    monkeypatch.setenv("XAGENT_RAGFLOW_URL", "http://127.0.0.1:9380")
    monkeypatch.setenv("XAGENT_RAGFLOW_API_KEY", "test-secret")
    factory = StorageFactory.get_factory()
    factory.reset_all()
    yield get_metadata_store()
    factory.reset_all()


async def search_api(name, actor):
    return await kb.search(
        collection=name,
        query_text="question",
        embedding_model_id=None,
        search_type=None,
        top_k=None,
        filters=None,
        fusion_config=None,
        rerank_model_id=None,
        rerank_top_k=None,
        readonly=None,
        nprobes=None,
        refine_factor=None,
        fallback_to_sparse=None,
        _user=actor,
        db=None,
    )


@pytest.mark.asyncio
async def test_bound_dataset_search_keeps_remote_ids_and_disables_local_rerank(
    remote_store, monkeypatch
):
    monkeypatch.setattr(
        RagflowClient, "list_datasets", lambda self: [{"id": "ds42", "name": "Remote"}]
    )
    owner = SimpleNamespace(id=41, is_admin=True)
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(
            name="remote", dataset_id="ds42", rerank_id="remote-model"
        ),
        _user=owner,
    )
    listed = await kb.list_collections_api(_user=owner, db=None)
    entry = next(item for item in listed.collections if item.name == "remote")
    assert entry.extra_metadata["kb_storage"]["backend"] == "ragflow"
    assert entry.embeddings == 0
    assert (
        await kb.get_collection_api("remote", _user=owner, db=None)
    ).name == "remote"

    calls = []

    def retrieve(self, dataset_id, question, *, top_k, rerank_id):
        calls.append((dataset_id, question, top_k, rerank_id))
        return [
            {
                "id": "chunk-a",
                "content": "first",
                "document_id": "doc-a",
                "document_keyword": "a.pdf",
                "dataset_id": "ds42",
                "similarity": 3.8,
            },
            {
                "id": "chunk-b",
                "content": "second",
                "document_id": "doc-b",
                "document_keyword": "b.pdf",
                "dataset_id": "ds42",
                "similarity": -2.0,
            },
        ]

    monkeypatch.setattr(RagflowClient, "retrieve", retrieve)
    result = run_document_search(
        "remote",
        "question",
        config=SearchConfig(
            top_k=2,
            embedding_model_id="unavailable-local-model",
            rerank_model_id="unavailable-local-reranker",
        ),
        user_id=41,
        is_admin=False,
    )
    assert calls == [("ds42", "question", 2, "remote-model")]
    assert result.status == "success"
    assert [(r.doc_id, r.chunk_id, r.score, r.parse_hash) for r in result.results] == [
        ("doc-a", "chunk-a", 1.0, ""),
        ("doc-b", "chunk-b", 0.5, ""),
    ]
    assert result.results[0].metadata["score_kind"] == "reciprocal_rank"
    assert result.results[1].metadata["ragflow_similarity"] == -2.0
    assert result.results[0].metadata["document_keyword"] == "a.pdf"
    assert "source" not in result.results[0].metadata

    changed = await kb_ragflow.update_binding(
        "remote", kb_ragflow.BindingUpdate(rerank_id=None), _user=owner, db=None
    )
    assert changed.rerank_id is None
    run_document_search(
        "remote",
        "question",
        config=SearchConfig(top_k=2, embedding_model_id=""),
        user_id=41,
    )
    assert calls[-1] == ("ds42", "question", 2, None)


@pytest.mark.asyncio
async def test_only_admin_can_discover_and_bind_existing_datasets(
    remote_store, monkeypatch
):
    app = FastAPI()
    app.include_router(kb_ragflow.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=42, is_admin=False
    )
    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: (_ for _ in ()).throw(AssertionError("non-admin accessed remote")),
    )
    client = TestClient(app)
    assert client.get("/api/kb/ragflow/datasets").status_code == 403
    assert (
        client.post(
            "/api/kb/ragflow/bindings", json={"name": "private", "dataset_id": "ds42"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/kb/ragflow/bindings/private", json={"rerank_id": None}
        ).status_code
        == 403
    )

    monkeypatch.setattr(
        RagflowClient, "list_datasets", lambda self: [{"id": "ds42", "name": "Remote"}]
    )
    with pytest.raises(HTTPException) as absent:
        await kb_ragflow.create_binding(
            kb_ragflow.BindingRequest(name="invalid", dataset_id="unknown"),
            _user=SimpleNamespace(id=41, is_admin=True),
        )
    assert absent.value.status_code == 422
    assert await remote_store.get_collection_config("invalid", 41) is None


@pytest.mark.asyncio
async def test_remote_binding_denies_other_tenants_before_network(
    remote_store, monkeypatch
):
    monkeypatch.setattr(
        RagflowClient, "list_datasets", lambda self: [{"id": "ds42", "name": "Remote"}]
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="private", dataset_id="ds42"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )

    def unexpected_retrieve(*_args, **_kwargs):
        raise AssertionError("Unauthorized retrieval crossed the remote boundary")

    monkeypatch.setattr(RagflowClient, "retrieve", unexpected_retrieve)
    with pytest.raises(PermissionError, match="access denied"):
        run_document_search("private", "question", user_id=42, is_admin=False)
    with pytest.raises(HTTPException) as denied:
        await search_api("private", SimpleNamespace(id=42, is_admin=False))
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_remote_failure_is_not_empty_success_and_binding_deletion_is_local_only(
    remote_store, monkeypatch
):
    owner = SimpleNamespace(id=41, is_admin=True)
    monkeypatch.setattr(
        RagflowClient, "list_datasets", lambda self: [{"id": "ds42", "name": "Remote"}]
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="remote", dataset_id="ds42"), _user=owner
    )

    def unavailable(*_args, **_kwargs):
        raise RagflowError("RAGFlow request failed")

    monkeypatch.setattr(RagflowClient, "retrieve", unavailable)
    with pytest.raises(HTTPException) as failed:
        await search_api("remote", owner)
    assert failed.value.status_code == 503
    with pytest.raises(HTTPException) as readonly:
        await kb.save_collection_config("remote", _user=owner, db=None)
    assert readonly.value.status_code == 422
    with pytest.raises(ReadOnlyKnowledgeBaseError):
        await get_kb_coordinator().api_compatibility.save_collection_config(
            collection="remote", config_json="{}", user_id=41
        )

    deleted = await kb.delete_collection_api(
        "remote", _user=owner, db=SimpleNamespace(commit=lambda: None)
    )
    assert deleted.status == "success"
    assert await remote_store.get_collection_config("remote", 41) is None
    # No RAGFlow delete API is exposed or invoked: metadata/config alone are removed.
    assert "remote dataset unchanged" in deleted.message


@pytest.mark.asyncio
async def test_agent_scope_respects_declared_names_even_without_local_embeddings(
    remote_store, monkeypatch
):
    monkeypatch.setattr(
        RagflowClient, "list_datasets", lambda self: [{"id": "ds42", "name": "Remote"}]
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="remote", dataset_id="ds42"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="other", dataset_id="ds42"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    calls = []

    def retrieve(self, dataset_id, question, *, top_k, rerank_id):
        calls.append((dataset_id, rerank_id))
        return [
            {
                "id": "remote-chunk",
                "content": "remote answer",
                "document_id": "remote-document",
                "document_keyword": "source.pdf",
                "dataset_id": "ds42",
                "similarity": 0.8,
            }
        ]

    monkeypatch.setattr(RagflowClient, "retrieve", retrieve)
    listed = await list_knowledge_bases(
        ListKnowledgeBasesArgs(allowed_collections=["remote"]), user_id=41
    )
    assert [item["name"] for item in listed.knowledge_bases] == ["remote"]
    denied = await search_knowledge_base(
        KnowledgeSearchArgs(
            query="question", collections=["remote"], allowed_collections=[]
        ),
        user_id=41,
    )
    assert denied.results == []
    assert not calls
    scoped_tool = get_knowledge_search_tool(allowed_collections=[], user_id=41)
    spoofed = await scoped_tool.run_json_async(
        {
            "query": "question",
            "collections": ["remote"],
            "allowed_collections": ["remote"],
        }
    )
    assert spoofed.results == []
    assert not calls
    excluded = await search_knowledge_base(
        KnowledgeSearchArgs(
            query="question", collections=["other"], allowed_collections=["remote"]
        ),
        user_id=41,
    )
    assert excluded.results == []
    assert not calls
    allowed = await search_knowledge_base(
        KnowledgeSearchArgs(query="question", allowed_collections=["remote"]),
        user_id=41,
    )
    assert [(item.doc_id, item.chunk_id) for item in allowed.results] == [
        ("remote-document", "remote-chunk")
    ]
    assert calls == [("ds42", None)]
    with snapshot_knowledge_base_team_hooks():
        set_knowledge_base_team_hooks(
            team_visibility=lambda _db, *, team_id: (
                [
                    KnowledgeBaseAccess(
                        name="remote",
                        storage_user_id=41,
                        team_owned=True,
                        can_edit=False,
                        can_delete=False,
                    )
                ]
                if team_id == 7
                else []
            )
        )
        shared = await search_knowledge_base(
            KnowledgeSearchArgs(query="question", allowed_collections=["remote"]),
            user_id=42,
            governing_team_id=7,
            agent_creator_user_id=41,
            declared_knowledge_bases=["remote"],
        )
        assert [(item.doc_id, item.chunk_id) for item in shared.results] == [
            ("remote-document", "remote-chunk")
        ]
        assert calls == [("ds42", None), ("ds42", None)]

    def unavailable(*_args, **_kwargs):
        raise RagflowError("RAGFlow request failed")

    monkeypatch.setattr(RagflowClient, "retrieve", unavailable)
    failed = await search_knowledge_base(
        KnowledgeSearchArgs(query="question", allowed_collections=["remote"]),
        user_id=41,
    )
    assert failed.results == []
    assert "search failed" in failed.summary
    assert "No relevant documents found" not in failed.summary


@pytest.mark.asyncio
async def test_colliding_config_owner_cannot_inherit_global_remote_binding(
    remote_store, monkeypatch
):
    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [{"id": "winner", "name": "Remote"}],
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="collision", dataset_id="winner"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    # A losing publisher or pre-existing native owner must not inherit the
    # winner's remote authority merely because its config row exists.
    await remote_store.save_collection_config("collision", "{}", 42)
    calls = []
    monkeypatch.setattr(
        RagflowClient, "retrieve", lambda *args, **kwargs: calls.append(args) or []
    )
    for admin in (False, True):
        listed = await list_knowledge_bases(
            ListKnowledgeBasesArgs(), user_id=42, is_admin=admin
        )
        assert listed.knowledge_bases == []
        with pytest.raises(PermissionError):
            run_document_search("collision", "question", user_id=42, is_admin=admin)
    assert calls == []


@pytest.mark.asyncio
async def test_replacing_server_does_not_retarget_existing_binding(
    remote_store, monkeypatch
):
    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [{"id": "same-id", "name": "Remote"}],
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="scoped-server", dataset_id="same-id"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    monkeypatch.setattr(RagflowClient, "retrieve", lambda *args, **kwargs: [])
    monkeypatch.setenv("XAGENT_RAGFLOW_URL", "http://different-server:9380")
    with pytest.raises(RagflowError):
        run_document_search("scoped-server", "question", user_id=41, is_admin=True)
    removed = await kb.delete_collection_api(
        "scoped-server",
        _user=SimpleNamespace(id=41, is_admin=True),
        db=SimpleNamespace(commit=lambda: None),
    )
    assert removed.status == "success"
    assert await remote_store.get_collection_config("scoped-server", 41) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        SearchConfig(embedding_model_id="", filters={"doc_id": "excluded-document"}),
        SearchConfig(embedding_model_id="", search_type="sparse"),
    ],
)
async def test_remote_search_rejects_constraints_it_cannot_honor(
    remote_store, monkeypatch, config
):
    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [{"id": "dataset", "name": "Remote"}],
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="constrained", dataset_id="dataset"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    calls = []
    monkeypatch.setattr(
        RagflowClient, "retrieve", lambda *args, **kwargs: calls.append(args) or []
    )
    with pytest.raises(ValueError):
        run_document_search("constrained", "question", config=config, user_id=41)
    assert calls == []


@pytest.mark.asyncio
async def test_coordinator_remote_context_enforces_owner_and_read_only_capability(
    remote_store, monkeypatch
):
    from xagent.core.tools.core.RAG_tools.kb import KBAccessMode, KBContextRequest

    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [{"id": "context-id", "name": "Remote"}],
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="context-kb", dataset_id="context-id"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    coordinator = get_kb_coordinator()
    context = await coordinator.get_context(
        KBContextRequest(collection="context-kb", user_id=41)
    )
    assert context.capabilities.supports_search
    assert not context.capabilities.supports_raw_connection
    with pytest.raises(PermissionError):
        await coordinator.get_context(
            KBContextRequest(collection="context-kb", user_id=42, is_admin=True)
        )
    with pytest.raises(ReadOnlyKnowledgeBaseError):
        await coordinator.get_context(
            KBContextRequest(
                collection="context-kb", user_id=41, access_mode=KBAccessMode.WRITE
            )
        )


@pytest.mark.asyncio
async def test_admin_discovery_exposes_only_dataset_identity(remote_store, monkeypatch):
    app = FastAPI()
    app.include_router(kb_ragflow.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=41, is_admin=True
    )
    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [
            {
                "id": "dataset",
                "name": "Remote",
                "api_key": "private-provider-key",  # pragma: allowlist secret - public redaction fixture
            }
        ],
    )
    client = TestClient(app)
    response = client.get("/api/kb/ragflow/datasets")
    assert response.status_code == 200
    assert response.json() == {"datasets": [{"id": "dataset", "name": "Remote"}]}

    def unavailable(*args, **kwargs):
        raise RagflowError("private-provider-key")

    monkeypatch.setattr(RagflowClient, "list_datasets", unavailable)
    failed = client.get("/api/kb/ragflow/datasets")
    assert failed.status_code == 503
    assert "private-provider-key" not in failed.text


@pytest.mark.asyncio
async def test_corrupted_remote_publication_is_an_error_not_empty_search(
    remote_store, monkeypatch
):
    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [{"id": "dataset", "name": "Remote"}],
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="broken", dataset_id="dataset"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    info = await remote_store.get_collection("broken")
    info.extra_metadata["kb_storage"]["dataset_id"] = ""
    await remote_store.save_collection(info)
    calls = []
    monkeypatch.setattr(
        RagflowClient, "retrieve", lambda *args, **kwargs: calls.append(args) or []
    )
    with pytest.raises(RuntimeError):
        await search_knowledge_base(
            KnowledgeSearchArgs(query="question", allowed_collections=["broken"]),
            user_id=41,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_rejected_remote_ingestion_preserves_surrounding_native_operation(
    remote_store, monkeypatch, tmp_path
):
    from xagent.core.tools.core.RAG_tools.kb import (
        KBOperationCompatibilityFacade,
        KBPipelineCompatibilityFacade,
    )

    monkeypatch.setattr(
        RagflowClient,
        "list_datasets",
        lambda self: [{"id": "dataset", "name": "Remote"}],
    )
    await kb_ragflow.create_binding(
        kb_ragflow.BindingRequest(name="readonly", dataset_id="dataset"),
        _user=SimpleNamespace(id=41, is_admin=True),
    )
    operations = KBOperationCompatibilityFacade()
    facade = KBPipelineCompatibilityFacade(operation_compatibility=operations)
    with operations.start_operation(
        operation_type="document_ingestion", collection="native"
    ) as outer:
        with facade.web_page_operation(
            collection="native", url="https://example.com"
        ) as page:
            with pytest.raises(ReadOnlyKnowledgeBaseError):
                facade.run_document_ingestion(
                    "readonly", str(tmp_path / "unread.txt"), user_id=41
                )
            facade.finish_web_page_operation(
                page, status="success", message="Native work completed"
            )
    assert outer.outcome.status == "success"
    assert outer.outcome.child_outcomes == ()
