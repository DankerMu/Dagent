"""No internal ``_xagent_`` message key may reach compatible LAN providers."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

import pytest

import xagent.core.model.chat.basic as basic_pkg
from xagent.core.model.chat.basic.base import BaseLLM
from xagent.core.model.chat.basic.openai import OpenAICompatibleLLM, OpenAILLM
from xagent.core.model.chat.basic.xinference import XinferenceLLM
from xagent.core.model.chat.types import PROVIDER_STATE_METADATA_KEY

_MARKED_HISTORY: list[dict[str, Any]] = [
    {"role": "user", "content": "Search xagent"},
    {
        "role": "assistant",
        "content": "",
        PROVIDER_STATE_METADATA_KEY: {"gateway": {"reasoning_content": "prior"}},
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "result"},
]


def _assert_no_internal_keys(messages: list[dict[str, Any]]) -> None:
    for message in messages:
        for key in message:
            assert not key.startswith("_xagent_"), (
                f"leaked internal key {key!r} in outbound message {message!r}"
            )


@pytest.mark.asyncio
async def test_xinference_chat_strips_internal_keys_before_the_sdk_call(mocker):
    class _ModelHandle:
        async def chat(self, **kwargs: Any):
            self.received_messages = kwargs["messages"]
            return {
                "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]
            }

    llm = XinferenceLLM(model_name="qwen3.8")
    handle = _ModelHandle()
    llm._client = mocker.MagicMock()
    llm._model_handle = handle

    await llm.chat(_MARKED_HISTORY)

    _assert_no_internal_keys(handle.received_messages)


@pytest.mark.asyncio
async def test_xinference_stream_chat_strips_internal_keys_before_the_sdk_call(mocker):
    class _EmptyStream:
        def __aiter__(self) -> "_EmptyStream":
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    class _StreamHandle:
        async def chat(self, **kwargs: Any) -> _EmptyStream:
            self.received_messages = kwargs["messages"]
            return _EmptyStream()

    llm = XinferenceLLM(model_name="qwen3.8")
    handle = _StreamHandle()
    llm._client = mocker.MagicMock()
    llm._model_handle = handle

    async for _chunk in llm.stream_chat(_MARKED_HISTORY):
        pass

    _assert_no_internal_keys(handle.received_messages)


# OpenAILLM has its own wire-request regression in test_openai.py.
_STRIP_GUARD_COVERED: frozenset[type] = frozenset({XinferenceLLM, OpenAILLM})
_STRIP_GUARD_EXEMPT: dict[type, str] = {
    OpenAICompatibleLLM: "transport base; OpenAILLM exercises the same stripping path",
}


def _all_basellm_subclasses() -> set[type]:
    """Discover production subclasses, ignoring test-only doubles."""
    for _, module_name, _ in pkgutil.iter_modules(basic_pkg.__path__):
        importlib.import_module(f"{basic_pkg.__name__}.{module_name}")

    discovered: set[type] = set()

    def _walk(cls: type) -> None:
        for subclass in cls.__subclasses__():
            if subclass in discovered:
                continue
            discovered.add(subclass)
            _walk(subclass)

    _walk(BaseLLM)
    prefix = f"{basic_pkg.__name__}."
    return {cls for cls in discovered if cls.__module__.startswith(prefix)}


def test_every_basellm_subclass_is_accounted_for_by_the_strip_guard():
    discovered = _all_basellm_subclasses()
    unaccounted = discovered - _STRIP_GUARD_COVERED - set(_STRIP_GUARD_EXEMPT)
    assert not unaccounted, (
        "New BaseLLM subclass(es) with no _xagent_ leak-guard coverage: "
        f"{sorted(cls.__qualname__ for cls in unaccounted)}"
    )
