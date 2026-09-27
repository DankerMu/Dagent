"""Pattern callers may supply a LAN adapter with synchronous chat results."""

import pytest

from xagent.core.agent import PatternRuntime


@pytest.mark.asyncio
async def test_sync_model_response_is_returned_without_scheduling_a_background_call():
    class LocalModel:
        def chat(self, **kwargs):
            return {"content": "offline answer", "usage": {"total_tokens": 4}}

    runtime = PatternRuntime()
    result = await runtime.run_llm_call(
        LocalModel(), messages=[{"role": "user", "content": "hello"}]
    )
    assert result == {"content": "offline answer", "usage": {"total_tokens": 4}}
