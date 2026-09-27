"""Local model selection and persisted sharing through real SQLite rows."""

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.core.model.image.openai import OpenAIImageModel
from xagent.web.api.model import _resolve_asr_model_for_transcription, create_model
from xagent.web.models.database import Base
from xagent.web.models.model import Model
from xagent.web.models.user import User, UserDefaultModel, UserModel
from xagent.web.schemas.model import ModelCreate
from xagent.web.services.model_service import (
    get_asr_models,
    get_default_asr_model,
    get_default_tts_model,
    get_image_models,
    resolve_default_model_id,
    with_default_general_model,
)
from xagent.web.services.model_store import ModelStore


@pytest.fixture
def local_models_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_explicit_general_model_is_not_replaced_by_shared_admin_default(
    local_models_db,
):
    db = local_models_db
    admin = User(username="admin-local", password_hash="hash", is_admin=True)
    member = User(username="member-local", password_hash="hash", is_admin=False)
    db.add_all([admin, member])
    db.flush()
    shared = Model(
        model_id="shared-lan",
        category="llm",
        model_provider="openai-compatible",
        model_name="local",
        api_key="",
        base_url="http://127.0.0.1:8001/v1",
        is_active=True,
    )
    db.add(shared)
    db.flush()
    db.add_all(
        [
            UserModel(
                user_id=admin.id, model_id=shared.id, is_owner=True, is_shared=True
            ),
            UserDefaultModel(
                user_id=admin.id, model_id=shared.id, config_type="general"
            ),
        ]
    )
    db.commit()

    assert resolve_default_model_id(db, member.id, "general") == shared.id
    assert with_default_general_model(db, {}, user_id=member.id) == {
        "general": shared.id
    }
    assert with_default_general_model(db, {"general": None}, user_id=member.id) == {
        "general": None
    }
    assert with_default_general_model(db, {"general": 12345}, user_id=member.id) == {
        "general": 12345
    }

    shared.is_active = False
    db.commit()
    assert resolve_default_model_id(db, member.id, "general") is None
    assert with_default_general_model(db, {}, user_id=member.id) == {}


def test_local_image_model_without_api_key_honors_visibility_and_active_state(
    local_models_db,
):
    db = local_models_db
    admin = User(username="admin-image", password_hash="hash", is_admin=True)
    member = User(username="member-image", password_hash="hash", is_admin=False)
    db.add_all([admin, member])
    db.flush()
    shared = Model(
        model_id="image-lan",
        category="image",
        model_provider="openai-compatible",
        model_name="local-image",
        api_key="",
        base_url="http://127.0.0.1:8001/v1",
        abilities=["generate"],
        is_active=True,
    )
    hidden = Model(
        model_id="image-private",
        category="image",
        model_provider="openai-compatible",
        model_name="private-image",
        api_key="",
        base_url="http://127.0.0.1:8002/v1",
        abilities=["generate"],
        is_active=True,
    )
    db.add_all([shared, hidden])
    db.flush()
    db.add_all(
        [
            UserModel(
                user_id=admin.id, model_id=shared.id, is_owner=True, is_shared=True
            ),
            UserModel(
                user_id=admin.id, model_id=hidden.id, is_owner=True, is_shared=False
            ),
        ]
    )
    db.commit()

    visible = get_image_models(db, member.id)
    assert set(visible) == {"image-lan"}
    assert isinstance(visible["image-lan"], OpenAIImageModel)
    assert visible["image-lan"].base_url == "http://127.0.0.1:8001/v1"
    assert visible["image-lan"].api_key is None
    assert visible["image-lan"].abilities == ["generate"]

    shared.is_active = False
    db.commit()
    assert get_image_models(db, member.id) == {}


def test_model_listing_does_not_grant_owner_rights_on_borrowed_model(local_models_db):
    db = local_models_db
    admin = User(username="admin-owner", password_hash="hash", is_admin=True)
    member = User(username="member-borrower", password_hash="hash", is_admin=False)
    db.add_all([admin, member])
    db.flush()
    model = Model(
        model_id="borrowed-lan",
        category="llm",
        model_provider="openai-compatible",
        model_name="local-chat",
        api_key="",
        base_url="http://127.0.0.1:8001/v1",
        abilities=["chat"],
        is_active=True,
    )
    db.add(model)
    db.flush()
    db.add_all(
        [
            UserModel(
                user_id=admin.id, model_id=model.id, is_owner=True, is_shared=True
            ),
            UserModel(
                user_id=member.id,
                model_id=model.id,
                is_owner=False,
                can_edit=False,
                can_delete=False,
                is_shared=False,
            ),
        ]
    )
    db.commit()

    listed = ModelStore(db).list_models(
        user_id=member.id,
        skip=0,
        limit=20,
        model_provider="openai-compatible",
        category="llm",
        is_active=True,
    )
    assert [item.model_id for item in listed] == ["borrowed-lan"]
    assert listed[0].is_owner is False
    assert listed[0].can_edit is False
    assert listed[0].can_delete is False
    assert (
        ModelStore(db).list_models(
            user_id=member.id,
            skip=0,
            limit=20,
            model_provider="xinference",
            category="llm",
            is_active=True,
        )
        == []
    )


def test_explicit_asr_selection_refuses_inaccessible_or_incompatible_models(
    local_models_db,
):
    db = local_models_db
    admin = User(username="admin-asr", password_hash="hash", is_admin=True)
    member = User(username="member-asr", password_hash="hash", is_admin=False)
    db.add_all([admin, member])
    db.flush()
    hidden = Model(
        model_id="hidden-asr",
        category="speech",
        model_provider="xinference",
        model_name="private-asr",
        api_key="",
        base_url="http://127.0.0.1:9997",
        abilities=["asr"],
        is_active=True,
    )
    wrong = Model(
        model_id="not-asr",
        category="speech",
        model_provider="xinference",
        model_name="tts-only",
        api_key="",
        base_url="http://127.0.0.1:9997",
        abilities=["tts"],
        is_active=True,
    )
    available = Model(
        model_id="local-asr",
        category="speech",
        model_provider="xinference",
        model_name="lan-asr",
        api_key="",
        base_url="http://127.0.0.1:9997",
        abilities=["asr"],
        is_active=True,
    )
    db.add_all([hidden, wrong, available])
    db.flush()
    db.add_all(
        [
            UserModel(
                user_id=admin.id, model_id=hidden.id, is_owner=True, is_shared=False
            ),
            UserModel(
                user_id=member.id, model_id=wrong.id, is_owner=True, is_shared=False
            ),
            UserModel(
                user_id=member.id, model_id=available.id, is_owner=True, is_shared=False
            ),
            UserDefaultModel(user_id=member.id, model_id=hidden.id, config_type="asr"),
        ]
    )
    db.commit()

    with pytest.raises(HTTPException) as inaccessible:
        _resolve_asr_model_for_transcription(db, member, "hidden-asr")
    assert inaccessible.value.status_code == 404
    with pytest.raises(HTTPException) as incompatible:
        _resolve_asr_model_for_transcription(db, member, "not-asr")
    assert incompatible.value.status_code == 400
    assert (
        _resolve_asr_model_for_transcription(db, member, "local-asr").id == available.id
    )
    assert _resolve_asr_model_for_transcription(db, member).id == available.id


@pytest.mark.asyncio
async def test_lan_model_registration_accepts_missing_api_key(local_models_db):
    db = local_models_db
    member = User(username="lan-registrant", password_hash="hash", is_admin=False)
    db.add(member)
    db.commit()

    response = await create_model(
        ModelCreate(
            model_id="no-key-lan",
            model_provider="openai-compatible",
            model_name="local-chat",
            base_url="http://127.0.0.1:8001/v1",
            abilities=["chat"],
        ),
        db,
        member,
    )
    assert response.model_id == "no-key-lan"
    persisted = db.query(Model).filter(Model.model_id == "no-key-lan").one()
    assert persisted.api_key == ""
    assert persisted.base_url == "http://127.0.0.1:8001/v1"
    assert (
        db.query(UserModel)
        .filter(UserModel.user_id == member.id, UserModel.model_id == persisted.id)
        .one()
        .is_owner
        is True
    )


def test_model_store_default_tracks_persisted_assignment(local_models_db):
    db = local_models_db
    member = User(username="default-owner", password_hash="hash", is_admin=False)
    model = Model(
        model_id="default-lan",
        category="llm",
        model_provider="openai-compatible",
        model_name="local-chat",
        api_key="",
        base_url="http://127.0.0.1:8001/v1",
        is_active=True,
    )
    db.add_all([member, model])
    db.flush()
    store = ModelStore(db)
    assert store.get_user_default_model(member.id, "general") is None
    db.add(
        UserDefaultModel(user_id=member.id, model_id=model.id, config_type="general")
    )
    db.commit()
    assigned = store.get_user_default_model(member.id, "general")
    assert assigned is not None
    assert assigned.model_id == model.id
    assert assigned.user_id == member.id


def test_saved_local_speech_defaults_ignore_invisible_and_unreadable_models(
    local_models_db,
):
    db = local_models_db
    admin = User(username="speech-admin", password_hash="hash", is_admin=True)
    member = User(username="speech-member", password_hash="hash", is_admin=False)
    db.add_all([admin, member])
    db.flush()

    def speech_model(
        model_id, *, abilities, provider="xinference", base_url="http://127.0.0.1:9997"
    ):
        return Model(
            model_id=model_id,
            category="speech",
            model_provider=provider,
            model_name=model_id,
            api_key="",
            base_url=base_url,
            abilities=abilities,
            is_active=True,
        )

    hidden = speech_model("invisible-asr", abilities=["asr"])
    asr = speech_model("working-asr", abilities=["asr"])
    tts = speech_model("working-tts", abilities=["tts"])
    malformed = speech_model("malformed-abilities", abilities="not-json")
    no_endpoint = speech_model("missing-endpoint", abilities=["asr"], base_url=None)
    unsupported = speech_model(
        "unsupported-provider", abilities=["asr"], provider="legacy-provider"
    )
    db.add_all([hidden, asr, tts, malformed, no_endpoint, unsupported])
    db.flush()
    db.add_all(
        [
            UserModel(
                user_id=admin.id, model_id=hidden.id, is_owner=True, is_shared=False
            ),
            UserModel(user_id=member.id, model_id=asr.id, is_owner=True),
            UserModel(user_id=member.id, model_id=tts.id, is_owner=True),
            UserModel(user_id=member.id, model_id=malformed.id, is_owner=True),
            UserModel(user_id=member.id, model_id=no_endpoint.id, is_owner=True),
            UserModel(
                user_id=admin.id, model_id=unsupported.id, is_owner=True, is_shared=True
            ),
        ]
    )
    asr_default = UserDefaultModel(
        user_id=member.id, model_id=hidden.id, config_type="asr"
    )
    db.add_all(
        [
            asr_default,
            UserDefaultModel(user_id=member.id, model_id=tts.id, config_type="tts"),
        ]
    )
    db.commit()

    assert get_default_asr_model(member.id, db=db) is None
    listed = get_asr_models(db, member.id)
    assert set(listed) == {"working-asr"}
    assert listed["working-asr"].api_key is None

    asr_default.model_id = asr.id
    db.commit()
    selected_asr = get_default_asr_model(member.id, db=db)
    assert selected_asr.model == "working-asr"
    assert selected_asr.base_url == "http://127.0.0.1:9997"
    selected_tts = get_default_tts_model(member.id, db=db)
    assert selected_tts.model == "working-tts"

    # A persisted key made unreadable by a key rotation must not select an
    # unusable model, even if its ownership/default rows still exist.
    asr._api_key_encrypted = Fernet(Fernet.generate_key()).encrypt(b"old-key").decode()
    db.commit()
    assert get_default_asr_model(member.id, db=db) is None
    tts._api_key_encrypted = Fernet(Fernet.generate_key()).encrypt(b"old-key").decode()
    db.commit()
    assert get_default_tts_model(member.id, db=db) is None
