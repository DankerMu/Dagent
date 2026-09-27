"""Rerank selection persists per local collection owner without leaking permissions."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from xagent.core.tools.core.RAG_tools.core.schemas import (
    IngestionConfig,
    ListCollectionsResult,
)
from xagent.core.tools.core.RAG_tools.storage.factory import (
    StorageFactory,
    get_metadata_store,
)
from xagent.web.api import kb
from xagent.web.services.knowledge_base_team_scope import (
    KnowledgeBaseAccess,
    set_knowledge_base_team_hooks,
    snapshot_knowledge_base_team_hooks,
)


@pytest.mark.asyncio
async def test_rerank_binding_preserves_ingest_settings_and_is_scoped_per_owner(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LANCEDB_DIR", str(tmp_path / "kb"))
    factory = StorageFactory.get_factory()
    factory.reset_all()
    try:
        store = get_metadata_store()
        await store.save_collection_config(
            collection="local-docs",
            config_json=IngestionConfig(chunk_size=512).model_dump_json(),
            user_id=41,
        )
        first = SimpleNamespace(id=41, is_admin=True)
        second = SimpleNamespace(id=42, is_admin=True)

        assert (
            await kb.set_collection_rerank_model(
                collection="local-docs",
                rerank_model_id="  lan-rerank  ",
                _user=first,
                db=None,
            )
        ).status == "success"
        first_config = json.loads(
            await store.get_collection_config(collection="local-docs", user_id=41)
        )
        assert first_config["chunk_size"] == 512
        assert first_config["rerank_model_id"] == "lan-rerank"

        await kb.set_collection_rerank_model(
            collection="local-docs", rerank_model_id=None, _user=second, db=None
        )
        second_config = json.loads(
            await store.get_collection_config(collection="local-docs", user_id=42)
        )
        assert second_config.get("rerank_model_id") is None
        assert (
            json.loads(
                await store.get_collection_config(collection="local-docs", user_id=41)
            )["rerank_model_id"]
            == "lan-rerank"
        )
    finally:
        factory.reset_all()


@pytest.mark.asyncio
async def test_document_delete_fallback_requires_caller_owned_records(monkeypatch):
    async def list_collections(*, user_id, is_admin):
        assert (user_id, is_admin) == (41, False)
        return ListCollectionsResult(
            status="success", collections=[], total_count=0, message="ok"
        )

    class _OwnedRecordStore:
        def __init__(self):
            self.records = []
            self.unavailable = False

        def list_document_records(
            self, *, collection_name, user_id, is_admin, max_results
        ):
            assert (collection_name, user_id, is_admin, max_results) == (
                "local-docs",
                41,
                False,
                1,
            )
            if self.unavailable:
                raise OSError("document index unavailable")
            return self.records

    store = _OwnedRecordStore()
    monkeypatch.setattr(kb, "list_collections", list_collections)
    monkeypatch.setattr(kb, "get_vector_index_store", lambda: store)
    user = SimpleNamespace(id=41, is_admin=False)

    with pytest.raises(HTTPException) as denied:
        await kb._ensure_collection_access_for_document_delete("local-docs", user)
    assert denied.value.status_code == 403

    store.records = [{"doc_id": "owned"}]
    await kb._ensure_collection_access_for_document_delete("local-docs", user)

    store.unavailable = True
    with pytest.raises(HTTPException) as unavailable:
        await kb._ensure_collection_access_for_document_delete("local-docs", user)
    assert unavailable.value.status_code == 403


@pytest.mark.asyncio
async def test_failed_collection_listing_never_grants_collection_access(monkeypatch):
    attempts = []

    async def failed_listing(*, user_id, is_admin):
        attempts.append((user_id, is_admin))
        return ListCollectionsResult(
            status="error",
            collections=[],
            total_count=0,
            message="local metadata store unavailable",
        )

    async def no_wait(_delay):
        return None

    monkeypatch.setattr(kb, "list_collections", failed_listing)
    monkeypatch.setattr(kb.asyncio, "sleep", no_wait)
    with pytest.raises(HTTPException) as unavailable:
        await kb._ensure_collection_access(
            "local-docs", SimpleNamespace(id=41, is_admin=False), allow_create=True
        )
    assert unavailable.value.status_code == 503
    assert attempts == [(41, False)] * 3


@pytest.mark.asyncio
async def test_read_only_team_member_cannot_change_rerank_binding():
    user = SimpleNamespace(id=41, is_admin=False)
    with snapshot_knowledge_base_team_hooks():
        set_knowledge_base_team_hooks(
            access=lambda _db, _id, name, _action: KnowledgeBaseAccess(
                name=name,
                storage_user_id=42,
                team_owned=True,
                can_edit=False,
                can_delete=False,
            )
        )
        with pytest.raises(HTTPException) as denied:
            await kb.set_collection_rerank_model(
                collection="team-docs",
                rerank_model_id="local-rerank",
                _user=user,
                db=None,
            )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_invalid_collection_name_rejected_before_any_rerank_write():
    with pytest.raises(HTTPException) as invalid:
        await kb.set_collection_rerank_model(
            collection="tenant\\other-owner",
            rerank_model_id="local-rerank",
            _user=SimpleNamespace(id=41, is_admin=True),
            db=None,
        )
    assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_corrupt_existing_ingestion_config_is_not_overwritten(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LANCEDB_DIR", str(tmp_path / "kb"))
    factory = StorageFactory.get_factory()
    factory.reset_all()
    try:
        store = get_metadata_store()
        await store.save_collection_config(
            collection="local-docs", config_json="{invalid-json", user_id=41
        )
        with pytest.raises(HTTPException) as failed:
            await kb.set_collection_rerank_model(
                collection="local-docs",
                rerank_model_id="lan-rerank",
                _user=SimpleNamespace(id=41, is_admin=True),
                db=None,
            )
        assert failed.value.status_code == 500
        assert (
            await store.get_collection_config(collection="local-docs", user_id=41)
            == "{invalid-json"
        )
    finally:
        factory.reset_all()
