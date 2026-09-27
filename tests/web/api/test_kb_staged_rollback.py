"""Staged ingestion compensation must preserve a sibling's published document."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from xagent.core.tools.core.RAG_tools.core.schemas import IngestionResult
from xagent.web.api import kb


@pytest.mark.asyncio
@pytest.mark.parametrize("document_delete_fails", [False, True])
async def test_staged_rollback_preserves_sibling_and_cleans_input(
    monkeypatch, tmp_path, document_delete_fails
):
    documents = {
        "failed-doc": {"doc_id": "failed-doc"},
        "sibling-doc": {"doc_id": "sibling-doc"},
    }
    source = tmp_path / "staged.txt"
    source.write_text("failed ingestion input", encoding="utf-8")

    def delete_document(collection, doc_id, user_id, is_admin):
        assert (collection, user_id, is_admin) == ("shared", 5, False)
        if document_delete_fails:
            return SimpleNamespace(status="error", message="document store unavailable")
        documents.pop(doc_id)
        return SimpleNamespace(status="success")

    def delete_collection(*args):
        documents.clear()
        return SimpleNamespace(status="success")

    monkeypatch.setattr(kb, "delete_document", delete_document)
    monkeypatch.setattr(kb, "delete_collection", delete_collection)
    monkeypatch.setattr(
        kb,
        "get_vector_index_store",
        lambda: SimpleNamespace(
            list_document_records=lambda **kwargs: list(documents.values())
        ),
    )
    result = IngestionResult(
        status="error",
        message="embedding failed",
        doc_id="failed-doc",
        completed_steps=[{"name": "register_document", "metadata": {"created": True}}],
    )
    operation = kb._rollback_failed_staged_ingestion(
        db=MagicMock(),
        user=SimpleNamespace(id=5, is_admin=False),
        collection_name="shared",
        result=result,
        file_path=source,
        collection_existed_before=False,
        file_backup_path=None,
        had_existing_file=False,
    )
    if document_delete_fails:
        with pytest.raises(kb.RollbackFailureError):
            await operation
        assert "failed-doc" in documents
    else:
        await operation
        assert "failed-doc" not in documents
    assert "sibling-doc" in documents
    assert not source.exists()
