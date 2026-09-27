"""Retained LAN models expose meaningful provider, size, and usage boundaries."""

from types import SimpleNamespace

import httpx
import pytest
import requests

from xagent.core.model import EmbeddingModelConfig, ImageModelConfig, VideoModelConfig
from xagent.core.model.chat.token_context import TokenContextManager
from xagent.core.model.embedding.adapter import create_embedding_adapter
from xagent.core.model.embedding.openai import OpenAIEmbedding
from xagent.core.model.image.adapter import create_image_model
from xagent.core.model.image.base import resolve_generation_size
from xagent.core.model.image.usage import record_image_usage
from xagent.core.model.image.xinference import XinferenceImageModel
from xagent.core.model.video.adapter import create_video_model, retry_on

ENDPOINT = "http://model.lan/v1"


def test_embedding_factory_rejects_unknown_provider_and_missing_lan_endpoint(
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    config = EmbeddingModelConfig(
        id="embed-1",
        model_name="local-embed",
        model_provider="openai-compatible",
        base_url=ENDPOINT,
    )
    with pytest.raises(ValueError, match="Unsupported embedding provider: unknown"):
        create_embedding_adapter(
            config.model_copy(update={"model_provider": "unknown"})
        )
    with pytest.raises(ValueError, match="base_url is required"):
        create_embedding_adapter(config.model_copy(update={"base_url": None}))


def test_embedding_error_reports_a_provider_detail_when_error_alias_is_present(
    monkeypatch,
):
    response = requests.Response()
    response.status_code = 429
    response.url = ENDPOINT + "/embeddings"
    response._content = b'{"error": {"detail": "local model is loading"}}'
    monkeypatch.setattr(requests.Session, "post", lambda self, url, **kwargs: response)

    with pytest.raises(RuntimeError, match="local model is loading"):
        OpenAIEmbedding(model="local-embed", base_url=ENDPOINT).encode("hello")


def test_embedding_error_reports_plain_text_when_gateway_is_not_json(monkeypatch):
    response = requests.Response()
    response.status_code = 502
    response.url = ENDPOINT + "/embeddings"
    response._content = b"local gateway unavailable"
    monkeypatch.setattr(requests.Session, "post", lambda self, url, **kwargs: response)

    with pytest.raises(RuntimeError, match="local gateway unavailable"):
        OpenAIEmbedding(model="local-embed", base_url=ENDPOINT).encode("hello")


def test_xinference_embedding_adapter_encodes_through_selected_lan_model(monkeypatch):
    class Handle:
        def create_embedding(self, *, input):
            assert input == ["hello"]
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.5, 0.25])])

    class Server:
        def get_model(self, model_uid):
            assert model_uid == "local-embed"
            return Handle()

    monkeypatch.setattr(
        "xagent.core.model.embedding.xinference.XinferenceClient",
        lambda **kwargs: Server(),
    )
    config = EmbeddingModelConfig(
        id="embed-1",
        model_name="local-embed",
        model_provider="xinference",
        base_url=ENDPOINT,
        max_retries=1,
    )
    assert create_embedding_adapter(config).encode("hello") == [0.5, 0.25]


def test_image_size_precedence_uses_single_requested_dimension_source(caplog):
    import logging

    logger = logging.getLogger("test.image.size")
    assert (
        resolve_generation_size(
            "512*512",
            resolution="768x384",
            width=640,
            height=480,
            aspect_ratio="16:9",
            separator="*",
            provider="Xinference",
            logger=logger,
        )
        == "512*512"
    )
    assert (
        resolve_generation_size(
            "512*512",
            resolution="768x384",
            width=640,
            height=480,
            aspect_ratio=None,
            separator="*",
            provider="Xinference",
            logger=logger,
        )
        == "768*384"
    )
    assert (
        resolve_generation_size(
            "512*512",
            resolution=None,
            width=640,
            height=480,
            aspect_ratio=None,
            separator="*",
            provider="Xinference",
            logger=logger,
        )
        == "640*480"
    )
    assert "aspect_ratio" in caplog.text


def test_provider_reported_dimensions_and_modality_counts_win_over_request():
    with TokenContextManager() as manager:
        record_image_usage(
            {
                "usage": {
                    "prompt_tokens": 8,
                    "input_tokens_details": {
                        "text_tokens": "3",
                        "image_tokens": "5",
                    },
                },
                "width": 320,
                "height": 240,
            },
            model_name="local-image",
            resolution="1024x1024",
            reported_size_from={"width": 320, "height": 240},
        )
        entry = manager.get_usage().details[0]

    assert entry["resolution"] == "320x240"
    assert entry["provider_input_tokens"] == 8
    assert entry["provider_text_input_tokens"] == 3
    assert entry["provider_image_input_tokens"] == 5


def test_unreadable_text_modality_does_not_discard_valid_image_modality(caplog):
    class Details:
        @property
        def text_tokens(self):
            raise RuntimeError("text count unavailable")

        image_tokens = 4

    with TokenContextManager() as manager:
        record_image_usage(
            {"usage": {"prompt_tokens": 8, "input_tokens_details": Details()}},
            model_name="local-image",
        )
        entry = manager.get_usage().details[0]

    assert entry["provider_input_tokens"] == 8
    assert entry["provider_image_input_tokens"] == 4
    assert "provider_text_input_tokens" not in entry
    assert "Reading optional usage field 'text_tokens' failed" in caplog.text


def test_unreadable_modality_container_still_records_billed_image_call(caplog):
    class Usage:
        prompt_tokens = 8
        completion_tokens = 2

        @property
        def input_tokens_details(self):
            raise RuntimeError("details unavailable")

    with TokenContextManager() as manager:
        record_image_usage({"usage": Usage()}, model_name="local-image")
        usage = manager.get_usage()

    assert usage.media_calls == 1
    assert usage.details[0]["provider_input_tokens"] == 8
    assert usage.details[0]["provider_output_tokens"] == 2
    assert "provider_text_input_tokens" not in usage.details[0]
    assert "Reading input token details failed" in caplog.text


def test_image_and_video_adapters_reject_unsupported_providers():
    config = ImageModelConfig(
        id="image-1",
        model_name="local-image",
        model_provider="unknown",
        base_url=ENDPOINT,
    )
    with pytest.raises(ValueError, match="Unsupported image model provider: unknown"):
        create_image_model(config)
    with pytest.raises(ValueError, match="Unsupported video model provider: unknown"):
        create_video_model(
            VideoModelConfig(
                id="video-1",
                model_name="local-video",
                model_provider="unknown",
                base_url=ENDPOINT,
            )
        )


def test_video_retry_distinguishes_transient_http_status_from_permanent_error():
    request = httpx.Request("POST", ENDPOINT + "/videos")
    for status, expected in [(429, True), (503, True), (400, False)]:
        response = httpx.Response(status, request=request)
        error = httpx.HTTPStatusError(
            "video response", request=request, response=response
        )
        assert retry_on(error) is expected


@pytest.mark.asyncio
async def test_xinference_image_rejects_unsupported_edit_before_billing(monkeypatch):
    class Server:
        def get_model(self, model_uid):
            return object()  # The selected model has no image_to_image operation.

    monkeypatch.setattr(
        "xagent.core.model.image.xinference.XinferenceClient", lambda **kwargs: Server()
    )
    model = XinferenceImageModel(
        model_name="local-image", base_url=ENDPOINT, abilities=["edit"]
    )
    with pytest.raises(RuntimeError, match="Image editing is not supported"):
        await model.edit_image("input.png", "change background")


@pytest.mark.asyncio
async def test_xinference_generation_preserves_transport_cause_for_retry(monkeypatch):
    class Handle:
        def text_to_image(self, **kwargs):
            raise requests.exceptions.ConnectionError("LAN connection lost")

    class Server:
        def get_model(self, model_uid):
            return Handle()

    monkeypatch.setattr(
        "xagent.core.model.image.xinference.XinferenceClient",
        lambda **kwargs: Server(),
    )
    model = XinferenceImageModel(
        model_name="local-image", base_url=ENDPOINT, abilities=["generate"]
    )
    with pytest.raises(
        RuntimeError, match="Xinference image generation failed"
    ) as error:
        await model.generate_image("mountain")
    assert isinstance(error.value.__cause__, requests.exceptions.ConnectionError)
