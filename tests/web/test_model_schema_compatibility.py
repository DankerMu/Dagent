"""Persisted model categories retain their validation contracts without cloud setup."""

import pytest
from pydantic import ValidationError

from xagent.web.schemas.model import ModelCreate, ModelUpdate


@pytest.mark.parametrize(
    ("category", "valid_ability", "wrong_ability"),
    [
        ("image", "edit", "chat"),
        ("embedding", "embedding", "generate"),
        ("speech", "asr", "embedding"),
        ("rerank", "rerank", "chat"),
        ("video", "generate", "asr"),
        ("sound_effect", "generate", "tts"),
        ("music", "generate", "asr"),
    ],
)
def test_model_category_refuses_incompatible_or_empty_abilities(
    category, valid_ability, wrong_ability
):
    request = {
        "model_id": "lan-model",
        "model_provider": "openai-compatible",
        "model_name": "local-model",
        "base_url": "http://127.0.0.1:8001/v1",
        "category": category,
    }
    assert ModelCreate(**request, abilities=[valid_ability]).abilities == [
        valid_ability
    ]
    with pytest.raises(ValidationError):
        ModelCreate(**request, abilities=[wrong_ability])
    with pytest.raises(ValidationError):
        ModelCreate(**request, abilities=[])
    with pytest.raises(ValidationError):
        ModelUpdate(category=category, abilities=[wrong_ability])


def test_model_patch_without_category_preserves_existing_persisted_abilities():
    # An update without the stored category cannot reinterpret historical rows.
    update = ModelUpdate(abilities=["asr"], api_key=None)
    assert update.abilities == ["asr"]
    assert update.api_key is None
