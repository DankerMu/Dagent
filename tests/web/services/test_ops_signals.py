"""Tests for the degraded-mode ops-signal registry."""

from __future__ import annotations

from xagent.web.services.ops_signals import (
    CHECKPOINT_DECODE_FALLBACK,
    active_degradations,
    clear_degradation,
    register_degradation,
)


def test_register_and_clear_degradation() -> None:
    clear_degradation(CHECKPOINT_DECODE_FALLBACK)
    assert CHECKPOINT_DECODE_FALLBACK not in active_degradations()
    register_degradation(CHECKPOINT_DECODE_FALLBACK, "detail one")
    register_degradation(CHECKPOINT_DECODE_FALLBACK, "detail two")
    assert active_degradations()[CHECKPOINT_DECODE_FALLBACK] == "detail two"
    clear_degradation(CHECKPOINT_DECODE_FALLBACK)
    clear_degradation(CHECKPOINT_DECODE_FALLBACK)  # idempotent
    assert CHECKPOINT_DECODE_FALLBACK not in active_degradations()
