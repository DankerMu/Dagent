"""Protocol errors and goal scopes must survive the observable turn boundary."""

from xagent.core.model.chat.tool_protocol import (
    TOOL_PROTOCOL_ERROR_KEY,
    ToolProtocolViolation,
    get_tool_protocol_error,
    tool_protocol_error_response,
)
from xagent.core.model.chat.types import ChunkType, StreamChunk
from xagent.core.model.intent import current_goal, goal_scope


def test_protocol_violation_never_looks_like_a_tool_call_turn():
    raw = {"provider_reply": [{"id": "broken"}]}
    response = tool_protocol_error_response(
        ToolProtocolViolation(
            provider="local-chat", code="missing_id", message="bad call"
        ),
        raw=raw,
    )

    assert response["type"] == "tool_protocol_error"
    assert response["tool_calls"] == []
    assert response["raw"] is raw
    assert get_tool_protocol_error(response) == {
        "provider": "local-chat",
        "code": "missing_id",
        "message": "bad call",
    }
    assert TOOL_PROTOCOL_ERROR_KEY in response


def test_stream_end_marker_is_distinct_from_usage_and_error():
    end = StreamChunk(type=ChunkType.END, finish_reason="stop")
    usage = StreamChunk(type=ChunkType.USAGE, usage={"prompt_tokens": 2})
    assert end.is_end()
    assert not usage.is_end()


def test_nested_goal_scopes_restore_outer_goal_even_after_exception():
    before = current_goal()
    with goal_scope("customer request"):
        assert current_goal() == "customer request"
        try:
            with goal_scope("subtask"):
                assert current_goal() == "subtask"
                raise ValueError("failed subtask")
        except ValueError:
            assert current_goal() == "customer request"
    assert current_goal() == before
