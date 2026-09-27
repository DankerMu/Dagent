"""Saved local media defaults execute through selected LAN model transports."""

from importlib import import_module

import pytest
from aiohttp import web
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.database import Base
from xagent.web.models.model import Model
from xagent.web.models.user import User, UserDefaultModel, UserModel
from xagent.web.tools.config import WebToolConfig


@pytest.fixture
def local_models(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'media-defaults.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr("xagent.web.models.database.get_session_local", lambda: factory)
    with factory() as db:
        admin = User(username="media-admin", password_hash="hash", is_admin=True)
        member = User(username="media-member", password_hash="hash", is_admin=False)
        db.add_all([admin, member])
        db.flush()
        yield db, admin, member
    engine.dispose()


@pytest.fixture
async def media_gateway():
    app = web.Application()

    async def generate_image(request):
        payload = await request.json()
        if payload.get("model") != "lan-image":
            return web.json_response({"error": "wrong model"}, status=400)
        return web.json_response({"data": [{"url": "http://127.0.0.1/generated.png"}]})

    async def edit_image(request):
        payload = await request.post()
        if payload.get("model") != "lan-image":
            return web.json_response({"error": "wrong model"}, status=400)
        return web.json_response({"data": [{"url": "http://127.0.0.1/edited.png"}]})

    async def generate_video(request):
        payload = await request.json()
        if payload.get("model") != "lan-video":
            return web.json_response({"error": "wrong model"}, status=400)
        return web.json_response(
            {"data": [{"task_id": "lan-job", "url": "http://127.0.0.1/clip.mp4"}]}
        )

    app.router.add_post("/v1/images/generations", generate_image)
    app.router.add_post("/v1/images/edits", edit_image)
    app.router.add_post("/v1/video/generations", generate_video)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_saved_media_defaults_refuse_hidden_model_then_execute_visible_lan_models(
    local_models, media_gateway, tmp_path, monkeypatch
):
    db, admin, member = local_models

    def saved_model(model_id, category, provider, abilities):
        model = Model(
            model_id=model_id,
            category=category,
            model_provider=provider,
            model_name=model_id,
            api_key="",
            base_url=f"{media_gateway}/v1"
            if provider == "openai-compatible"
            else media_gateway,
            abilities=abilities,
            is_active=True,
        )
        db.add(model)
        db.flush()
        return model

    hidden = saved_model("hidden-image", "image", "openai-compatible", ["generate"])
    image = saved_model("lan-image", "image", "openai-compatible", ["generate", "edit"])
    video = saved_model("lan-video", "video", "xinference", ["generate"])
    asr = saved_model("lan-asr", "speech", "xinference", ["asr"])
    tts = saved_model("lan-tts", "speech", "xinference", ["tts"])
    embedding = saved_model(
        "lan-embedding", "embedding", "openai-compatible", ["embedding"]
    )
    rerank = saved_model("lan-rerank", "rerank", "xinference", ["rerank"])
    hidden_embedding = saved_model(
        "private-embedding", "embedding", "openai-compatible", ["embedding"]
    )
    hidden_rerank = saved_model("private-rerank", "rerank", "xinference", ["rerank"])

    db.add(UserModel(user_id=admin.id, model_id=hidden.id, is_owner=True))
    db.add(UserModel(user_id=admin.id, model_id=hidden_embedding.id, is_owner=True))
    db.add(UserModel(user_id=admin.id, model_id=hidden_rerank.id, is_owner=True))
    for model in (image, video, asr, tts, embedding, rerank):
        db.add(UserModel(user_id=member.id, model_id=model.id, is_owner=True))
    defaults = {
        "image": hidden,
        "image_edit": image,
        "video": video,
        "asr": asr,
        "tts": tts,
        "embedding": embedding,
        "rerank": rerank,
    }
    for kind, model in defaults.items():
        db.add(UserDefaultModel(user_id=member.id, model_id=model.id, config_type=kind))
    db.commit()

    cfg = WebToolConfig(db=db, request=None, user=member, user_id=member.id)
    assert cfg.get_image_generate_model() is None
    saved_default = (
        db.query(UserDefaultModel)
        .filter_by(user_id=member.id, config_type="image")
        .one()
    )
    saved_default.model_id = image.id
    db.commit()

    image_output = await cfg.get_image_generate_model().generate_image("a local map")
    assert image_output["image_url"] == "http://127.0.0.1/generated.png"
    input_image = tmp_path / "input.png"
    input_image.write_bytes(b"\x89PNG\r\n\x1a\nlocal-input")
    edited = await cfg.get_image_edit_model().edit_image(str(input_image), "mark route")
    assert edited["image_url"] == "http://127.0.0.1/edited.png"

    video_result = await cfg.get_video_model().generate_video(prompt="local tour")
    assert video_result["task_id"] == "lan-job"
    assert video_result["video_url"] == "http://127.0.0.1/clip.mp4"

    class AudioHandle:
        def __init__(self, model_uid):
            self.model_uid = model_uid

        async def transcriptions(self, audio, **kwargs):
            return {
                "text": "recognized local speech"
                if self.model_uid == "lan-asr" and audio == b"lan-audio"
                else "wrong model or audio"
            }

        async def speech(self, input, **kwargs):
            return (
                b"LAN VOICE"
                if self.model_uid == "lan-tts" and input == "Speak locally"
                else b"wrong model or speech"
            )

    class LanXinferenceClient:
        def __init__(self, base_url, api_key=None):
            self.base_url = base_url

        async def get_model(self, model_uid):
            return AudioHandle(model_uid)

    try:
        sdk = import_module("xinference.client.restful.async_restful_client")
    except ImportError:
        sdk = import_module("xinference_client.client.restful.async_restful_client")
    monkeypatch.setattr(sdk, "AsyncClient", LanXinferenceClient)
    assert (
        await cfg.get_asr_model().transcribe(b"lan-audio") == "recognized local speech"
    )
    assert await cfg.get_tts_model().synthesize("Speak locally") == b"LAN VOICE"

    assert cfg.get_embedding_model() == "lan-embedding"
    assert cfg.get_rerank_model() == "lan-rerank"
    db.query(UserDefaultModel).filter_by(
        user_id=member.id, config_type="embedding"
    ).one().model_id = hidden_embedding.id
    db.query(UserDefaultModel).filter_by(
        user_id=member.id, config_type="rerank"
    ).one().model_id = hidden_rerank.id
    db.commit()
    no_cross_user_defaults = WebToolConfig(
        db=db, request=None, user=member, user_id=member.id
    )
    assert no_cross_user_defaults.get_embedding_model() is None
    assert no_cross_user_defaults.get_rerank_model() is None
