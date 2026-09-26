"""Provider cache prefixes survive turn-specific time and request changes."""

from datetime import datetime, timezone

from xagent.core.agent import ExecutionContext, ReActPattern
from xagent.core.file_ref import FILE_REF_MODEL_INSTRUCTIONS


def test_react_static_instructions_precede_turn_specific_context():
    pattern = ReActPattern()
    prompts = []
    for hour, request in ((1, "Inspect record 17"), (2, "Inspect record 42")):
        context = ExecutionContext(
            system_prompt="Follow the inventory policy.",
            created_at=datetime(2026, 9, 26, hour, tzinfo=timezone.utc),
        )
        context.add_user_message(request)
        messages = pattern._messages_for_llm(
            context, has_tools=True, tool_names=["lookup_record", "final_answer"]
        )
        assert [message["role"] for message in messages] == ["system", "user"]
        prompt = messages[0]["content"]
        assert prompt.startswith(context.system_prompt)
        assert request in prompt
        assert context._current_clock_text() in prompt
        assert FILE_REF_MODEL_INSTRUCTIONS in prompt
        assert messages[-1]["content"] == request
        prompts.append(prompt)
    # The entire shared instruction prefix must remain reusable, not just a
    # hand-picked byte count that hides a clock before the tool policy.
    prefixes = [prompt.split("Turn started at:", 1)[0] for prompt in prompts]
    assert prefixes[0] == prefixes[1]
    assert "lookup_record" in prefixes[0]
    assert "Use available tools" in prefixes[0]


def test_context_without_pattern_preserves_clock_and_stable_file_rules():
    context = ExecutionContext()
    context.add_user_message("What changed today?")
    before = context.get_messages_for_llm()
    assert before == context.get_messages_for_llm()
    prompt = before[0]["content"]
    assert prompt.index(FILE_REF_MODEL_INSTRUCTIONS) < prompt.index("Turn started at:")
    assert context.get_messages_for_llm(include_system=False) == [before[-1]]
