"""Tests for default_image_abilities."""

import pytest

from xagent.core.model.image.base import default_image_abilities

GENERATE = ["generate"]
BOTH = ["generate", "edit"]


@pytest.mark.parametrize(
    ("provider", "model_name", "expected"),
    [
        ("openai-compatible", "lan-image", BOTH),
        ("openai-compatible", "unknown", BOTH),
        ("xinference", "sd-3.5", BOTH),
        ("dashscope", "qwen-image-edit", GENERATE),
        ("gemini", "gemini-3-pro-image-preview", GENERATE),
        ("openai", "gpt-image-1", BOTH),
        ("openai", "some-unknown-name", BOTH),
        ("xinference", "my-image-edit", BOTH),
        ("xinference", "sd-3.5", BOTH),
        ("unknown-provider", "something-edit", GENERATE),
    ],
)
def test_default_image_abilities(
    provider: str, model_name: str, expected: list[str]
) -> None:
    assert default_image_abilities(provider, model_name) == expected
