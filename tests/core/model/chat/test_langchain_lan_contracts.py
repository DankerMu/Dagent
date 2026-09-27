"""The LangChain adapter keeps one retry policy across plain and bound calls."""

import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from xagent.core.model import ChatModelConfig, EmbeddingModelConfig
from xagent.core.model.chat.langchain import (
    ChatModelRetryWrapper,
    create_base_chat_model,
    create_base_chat_model_with_retry,
)
from xagent.core.retry.strategy import FixedDelay

ENDPOINT = "http://model.lan/v1"
ANSWER_SCHEMA = {
    "title": "Answer",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
}


def _completion(content: str = "ready") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "local-1",
            "object": "chat.completion",
            "created": 1,
            "model": "local-chat",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )


def _chat_client(handler):
    transport = httpx.MockTransport(handler)
    return ChatOpenAI(
        model="local-chat",
        api_key="local-token",  # pragma: allowlist secret - MockTransport credential
        base_url=ENDPOINT,
        max_retries=0,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def test_plain_invoke_retries_transient_lan_failure_without_sdk_retries():
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return _completion()

    model = ChatModelRetryWrapper(_chat_client(serve), FixedDelay(0), max_retries=2)

    assert model.invoke("hello").content == "ready"
    assert len(requests) == 2
    assert requests[0]["messages"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_async_bound_tools_retry_and_preserve_tool_choice_on_the_wire():
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return _completion()

    model = ChatModelRetryWrapper(
        _chat_client(serve),
        FixedDelay(0),
        max_retries=2,
    )
    tools = [
        {
            "name": "lookup",
            "description": "Look up a value",
            "parameters": {"type": "object", "properties": {}},
        }
    ]

    assert (
        await model.bind_tools(tools, tool_choice="lookup").ainvoke("find")
    ).content == "ready"
    assert len(requests) == 2
    assert requests[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "lookup"},
    }
    assert requests[1]["tools"][0]["function"]["name"] == "lookup"


@pytest.mark.asyncio
async def test_async_plain_call_uses_retry_on_transient_lan_failure():
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return _completion("recovered")

    model = ChatModelRetryWrapper(_chat_client(serve), FixedDelay(0), max_retries=2)
    assert (await model.ainvoke("hello")).content == "recovered"
    assert len(requests) == 2


def test_structured_output_is_parsed_after_retry():
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return _completion('{"value": 7}')

    model = ChatModelRetryWrapper(
        _chat_client(serve),
        FixedDelay(0),
        max_retries=2,
    )
    parsed = model.with_structured_output(
        ANSWER_SCHEMA, method="json_mode", include_raw=True
    ).invoke("Return an answer")

    assert parsed["parsed"] == {"value": 7}
    assert parsed["parsing_error"] is None
    assert len(requests) == 2
    assert requests[1]["response_format"] == {"type": "json_object"}


def test_chat_factory_uses_explicit_endpoint_and_rejects_unsupported_provider(
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    model = ChatModelConfig(
        id="local-chat",
        model_name="local-chat",
        model_provider="xinference",
        base_url=ENDPOINT,
        max_retries=2,
    )
    assert (
        create_base_chat_model_with_retry(model, 0.3).model.openai_api_base == ENDPOINT
    )

    with pytest.raises(TypeError, match="Unsupported Chat model type"):
        create_base_chat_model(
            EmbeddingModelConfig(id="embed", model_name="embed"), None
        )
    with pytest.raises(ValueError, match="Unsupported LLM provider: cloud-chat"):
        create_base_chat_model(
            model.model_copy(update={"model_provider": "cloud-chat"}), None
        )
    with pytest.raises(ValueError, match="base_url is required"):
        create_base_chat_model(model.model_copy(update={"base_url": None}), None)
