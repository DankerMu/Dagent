"""Shared provider instructions, independent of live turn evidence."""

from ...file_ref import FILE_REF_MODEL_INSTRUCTIONS
from ..language import canonical_unpinned_request_language_policy


def render_system_instructions(pattern_instruction: str) -> str:
    return "\n\n".join(
        part
        for part in (
            FILE_REF_MODEL_INSTRUCTIONS,
            pattern_instruction,
            canonical_unpinned_request_language_policy(),
            "Conversation focus rules: answer the current user request in "
            "the final system context. Earlier user and assistant messages "
            "are context only; use them to resolve references and preserve "
            "continuity, but do not re-answer previous requests or repeat "
            "previous final answers unless the current user request "
            "explicitly asks to revise, continue, compare, or summarize them.",
            "The final system context records when this turn started. "
            "Real time keeps advancing while this turn runs, so treat that as "
            "the start of the turn rather than the exact current time. Use it "
            "as the reference for relative dates such as today, recent, latest, "
            "yesterday, and tomorrow. When the answer depends on the actual "
            "time now, call the get_current_time tool if it is available "
            "instead of computing from this value.",
        )
        if part.strip()
    )
