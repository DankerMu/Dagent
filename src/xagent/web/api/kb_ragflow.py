"""Admin-controlled, local-only bindings to read-only RAGFlow datasets."""

from __future__ import annotations

import asyncio
import json
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...config import get_ragflow_url
from ...core.tools.core.RAG_tools.core.schemas import CollectionInfo
from ...core.tools.core.RAG_tools.kb.ragflow_binding import (
    get_visible_ragflow_binding,
    ragflow_binding,
)
from ...core.tools.core.RAG_tools.ragflow.client import RagflowClient, RagflowError
from ...core.tools.core.RAG_tools.storage.factory import get_metadata_store
from ..auth_dependencies import get_current_user, is_admin_user
from ..config import sanitize_path_component
from ..models.database import get_db
from ..models.user import User
from .kb import _effective_knowledge_base_user, _list_collections_with_retry

router = APIRouter(prefix="/api/kb/ragflow", tags=["kb"])


class BindingRequest(BaseModel):
    name: str
    dataset_id: str
    rerank_id: str | None = None


class BindingUpdate(BaseModel):
    rerank_id: str | None = None


class BindingResponse(BaseModel):
    name: str
    dataset_id: str
    rerank_id: str | None


class DatasetItem(BaseModel):
    id: str
    name: str


class DatasetList(BaseModel):
    datasets: list[DatasetItem]


def _admin(user: User = Depends(get_current_user)) -> User:
    if not is_admin_user(user):
        raise HTTPException(403, "Admin access required")
    return user


def _rerank_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        raise HTTPException(422, "Reranker identifier must not be empty")
    return value


def _dataset_id(raw: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", raw):
        raise HTTPException(422, "Invalid RAGFlow dataset identifier")
    return raw


def _name(raw: str) -> str:
    try:
        return sanitize_path_component(raw, "collection")
    except ValueError:
        raise HTTPException(422, "Invalid knowledge base name") from None


@router.get("/datasets", response_model=DatasetList)
async def datasets(_user: User = Depends(_admin)) -> DatasetList:
    try:
        datasets = await asyncio.to_thread(RagflowClient().list_datasets)
    except RagflowError:
        raise HTTPException(503, "RAGFlow is unavailable") from None
    return DatasetList(
        datasets=[DatasetItem(id=item["id"], name=item["name"]) for item in datasets]
    )


@router.post("/bindings", response_model=BindingResponse)
async def create_binding(
    body: BindingRequest,
    _user: User = Depends(_admin),
) -> BindingResponse:
    name = _name(body.name)
    dataset_id = _dataset_id(body.dataset_id)
    rerank_id = _rerank_id(body.rerank_id)
    collections = await _list_collections_with_retry(
        user_id=None, is_admin=True, stage="ragflow_binding_name_check"
    )
    if any(item.name == name for item in collections.collections):
        raise HTTPException(409, "Knowledge base name is already in use")
    try:
        available = await asyncio.to_thread(RagflowClient().list_datasets)
    except RagflowError:
        raise HTTPException(503, "RAGFlow datasets could not be verified") from None
    if not any(dataset["id"] == dataset_id for dataset in available):
        raise HTTPException(422, "RAGFlow dataset is not available")

    store = get_metadata_store()
    if await store.get_collection_config(name, None, is_admin=True) is not None:
        raise HTTPException(409, "Knowledge base name is already in use")
    # A config row is the owner's ordinary KB visibility/ACL authority.
    # Never remove global metadata on a failed publish: another writer may
    # have won the name between the check and this write.
    claim = {
        "binding_id": uuid4().hex,
        "dataset_id": dataset_id,
        "owner_user_id": int(_user.id),
        "server_url": get_ragflow_url(),
    }
    await store.save_collection_config(
        name, json.dumps({"_ragflow_binding": claim}), int(_user.id)
    )
    try:
        await store.save_collection(
            CollectionInfo(
                name=name,
                extra_metadata={
                    "kb_storage": {
                        "backend": "ragflow",
                        **claim,
                        "rerank_id": rerank_id,
                    }
                },
            )
        )
    except Exception:
        try:
            await store.get_collection(name)
        except ValueError:
            # No published metadata: remove only this owner's config row.
            await store.delete_collection_metadata(
                name, int(_user.id), delete_orphaned_metadata=False
            )
        raise
    return BindingResponse(name=name, dataset_id=dataset_id, rerank_id=rerank_id)


@router.patch("/bindings/{name}", response_model=BindingResponse)
async def update_binding(
    name: str,
    body: BindingUpdate,
    _user: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> BindingResponse:
    name = _name(name)
    _effective_knowledge_base_user(db, _user, name, action="read")
    store = get_metadata_store()
    try:
        authorized_binding = await get_visible_ragflow_binding(
            name, user_id=int(_user.id)
        )
    except PermissionError:
        raise HTTPException(
            403, "Only the binding owner can change its reranker"
        ) from None
    try:
        info = await store.get_collection(name)
    except ValueError:
        raise HTTPException(422, "Not a RAGFlow knowledge base") from None
    binding = ragflow_binding(info)
    if binding is None:
        raise HTTPException(422, "Not a RAGFlow knowledge base")
    if binding != authorized_binding:
        raise HTTPException(
            409, "Knowledge base binding changed; reload before editing"
        )
    rerank_id = _rerank_id(body.rerank_id)
    metadata = dict(info.extra_metadata)
    metadata["kb_storage"] = {**binding, "rerank_id": rerank_id}
    await store.save_collection(info.model_copy(update={"extra_metadata": metadata}))
    return BindingResponse(
        name=name, dataset_id=binding["dataset_id"], rerank_id=rerank_id
    )
