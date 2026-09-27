"""Exercise the OpenAI-compatible SDK against an in-process LAN HTTP transport."""

import json

import httpx
import openai
import pytest
from openai.types.chat.chat_completion_chunk import ChoiceDelta

from xagent.core.model.chat.basic.openai import OpenAILLM, field_content
from xagent.core.model.chat.error import retry_on
from xagent.core.model.chat.exceptions import LLMRetryableError
from xagent.core.model.chat.types import ChunkType

ENDPOINT = "http://model.lan/v1"
MESSAGES = [{"role": "user", "content": "answer"}]


def _reply(content, *, reasoning=None, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return httpx.Response(
        200,
        json={
            "id": "local-1",
            "object": "chat.completion",
            "created": 1,
            "model": "local-chat",
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": message}
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )


def _client(handler):
    return openai.AsyncOpenAI(
        api_key="local-token",  # pragma: allowlist secret - MockTransport credential
        base_url=ENDPOINT,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _llm(handler, **kwargs):
    llm = OpenAILLM("local-chat", base_url=ENDPOINT, **kwargs)
    llm._client = _client(handler)
    return llm


@pytest.mark.asyncio
async def test_rejected_response_format_degrades_only_the_rejected_parameter():
    sent = []

    def serve(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "response_format is not supported",
                        "param": "response_format",
                        "code": "unsupported_parameter",
                    }
                },
            )
        return _reply("plain answer")

    llm = _llm(serve)
    try:
        answer = await llm.chat(MESSAGES, response_format={"type": "json_object"})
        assert answer["content"] == "plain answer"
        assert len(sent) == 2
        assert sent[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in sent[1]
        assert sent[1]["messages"] == MESSAGES
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_reasoning_that_corrupts_structured_answer_retries_with_thinking_disabled():
    sent = []

    def serve(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            return _reply("not-json", reasoning="analysis")
        return _reply('{"answer": 9}', reasoning="analysis")

    llm = _llm(serve, abilities=["thinking_mode"])
    try:
        answer = await llm.chat(MESSAGES, response_format={"type": "json_object"})
        assert answer["content"] == '{"answer": 9}'
        assert len(sent) == 2
        assert sent[1]["response_format"] == {"type": "json_object"}
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_stream_retains_reasoning_and_recovers_usage_from_final_chunk():
    sent = []
    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": None, "reasoning_content": "plan"},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {"index": 0, "delta": {"content": "done"}, "finish_reason": None}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    chunks[-1]["usage"] = {
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "total_tokens": 9,
    }

    def serve(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )

    llm = _llm(serve)
    try:
        stream = [chunk async for chunk in llm.stream_chat(MESSAGES)]
        tokens = [chunk for chunk in stream if chunk.type is ChunkType.TOKEN]
        usages = [chunk for chunk in stream if chunk.type is ChunkType.USAGE]
        assert [chunk.delta for chunk in tokens] == ["done"]
        assert tokens[0].raw["reasoning_content"] == "plan"
        assert [chunk.usage for chunk in usages] == [
            {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
        ]
        assert sent[0]["stream_options"] == {"include_usage": True}
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_stream_rate_limit_is_classified_retryable_without_sdk_retry():
    sent = []

    def serve(request):
        sent.append(request)
        return httpx.Response(429, json={"error": {"message": "busy"}})

    llm = _llm(serve)
    try:
        with pytest.raises(LLMRetryableError, match="rate limit exceeded"):
            async for _ in llm.stream_chat(MESSAGES):
                pass
        assert len(sent) == 1
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_stream_connection_failure_keeps_retryable_classification():
    sent = []

    def serve(request):
        sent.append(request)
        raise httpx.ConnectError("LAN peer disconnected", request=request)

    llm = _llm(serve)
    try:
        with pytest.raises(LLMRetryableError, match="stream connection failed"):
            async for _ in llm.stream_chat(MESSAGES):
                pass
        assert len(sent) == 1
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_stream_auth_failure_is_permanent_not_retryable():
    def serve(request):
        return httpx.Response(401, json={"error": {"message": "invalid LAN token"}})

    llm = _llm(serve)
    try:
        with pytest.raises(RuntimeError) as failure:
            async for _ in llm.stream_chat(MESSAGES):
                pass
        assert retry_on(failure.value) is False
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_provider_auth_failure_is_not_hidden_as_transient_retry():
    def serve(request):
        return httpx.Response(401, json={"error": {"message": "bad token"}})

    llm = _llm(serve)
    try:
        with pytest.raises(RuntimeError) as failure:
            await llm.chat(MESSAGES)
        assert retry_on(failure.value) is False
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_provider_gateway_error_metadata_is_bounded_in_public_error():
    raw = "s" * 5000

    def serve(request):
        return httpx.Response(
            400,
            json={
                "error": {"message": "unsupported request", "metadata": {"raw": raw}}
            },
        )

    llm = _llm(serve)
    try:
        with pytest.raises(RuntimeError, match="OpenAI bad request") as error:
            await llm.chat(MESSAGES)
        detail = str(error.value).split("provider_raw=", 1)[1]
        assert "<truncated " in detail
        assert raw not in detail
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_model_listing_sorts_lan_models_and_requires_endpoint(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="base_url is required"):
        OpenAILLM("local-chat")
    with pytest.raises(ValueError, match="base_url is required"):
        await OpenAILLM.list_available_models("local-token")

    def serve(request):
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "old", "object": "model", "created": 1, "owned_by": "local"},
                    {"id": "new", "object": "model", "created": 9, "owned_by": "local"},
                ],
            },
        )

    monkeypatch.setattr(
        "xagent.core.model.chat.basic.openai.AsyncOpenAI",
        lambda **kwargs: _client(serve),
    )
    models = await OpenAILLM.list_available_models("local-token", ENDPOINT)
    assert [model["id"] for model in models] == ["new", "old"]


def test_declared_sdk_field_is_not_confused_with_an_unset_default():
    absent = ChoiceDelta(content=None)
    present = ChoiceDelta(content=None, reasoning_content="analysis")
    assert field_content(absent, "reasoning_content") == (False, None)
    assert field_content(present, "reasoning_content") == (True, "analysis")
    assert field_content({"reasoning_content": "explicit"}, "reasoning_content") == (
        True,
        "explicit",
    )
    assert field_content({}, "reasoning_content") == (False, None)


@pytest.mark.asyncio
async def test_structured_output_schema_reaches_lan_wire_in_compatible_format():
    sent = []

    def serve(request):
        sent.append(json.loads(request.content))
        return _reply('{"answer": 9}', reasoning="plan")

    llm = _llm(serve)
    try:
        answer = await llm.chat(
            MESSAGES,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "title": "Local Answer",
                        "type": "object",
                        "properties": {"answer": {"type": "integer"}},
                    },
                }
            },
        )
        assert answer["content"] == '{"answer": 9}'
        assert answer["reasoning_content"] == "plan"
        assert sent[0]["response_format"]["json_schema"]["name"] == "local_answer"
        assert sent[0]["response_format"]["json_schema"]["strict"] is True
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_tool_call_preserves_function_arguments_and_reasoning():
    def serve(request):
        payload = _reply(None, reasoning="choose lookup").json()
        payload["choices"][0]["finish_reason"] = "tool_calls"
        payload["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"key":"a"}'},
            }
        ]
        return httpx.Response(200, json=payload)

    llm = _llm(serve)
    try:
        result = await llm.chat(MESSAGES)
        assert result["type"] == "tool_call"
        assert result["tool_calls"][0]["function"] == {
            "name": "lookup",
            "arguments": '{"key":"a"}',
        }
        assert result["reasoning_content"] == "choose lookup"
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_empty_lan_completion_is_reported_instead_of_success():
    def serve(request):
        payload = _reply("unused").json()
        payload["choices"] = []
        return httpx.Response(200, json=payload)

    llm = _llm(serve)
    try:
        with pytest.raises(RuntimeError, match="Invalid API response: no choices"):
            await llm.chat(MESSAGES)
    finally:
        await llm.close()
