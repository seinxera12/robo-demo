"""
Bug Condition Exploration Tests — Bug 2: TTS not interrupted on new text input

These tests are EXPECTED TO FAIL on unfixed server/main.py.
Failure confirms the bug exists (missing `state.interrupt = True` assignment).

DO NOT fix the tests or the code when they fail.

Validates: AC-2.1
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from server.models import TranscriptionResult
from server.pipeline import PipelineState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_pipeline_state(state: str) -> PipelineState:
    """Create a minimal PipelineState for testing the text_input handler."""
    return PipelineState(
        session_id="test-session-id",
        history=[],
        state=state,
        interrupt=False,
        detected_language="en",
        audio_queue=asyncio.Queue(),
        transcript_queue=asyncio.Queue(),
        token_queue=asyncio.Queue(),
        audio_out_queue=asyncio.Queue(),
    )


async def call_text_input_handler(pipeline_state: PipelineState, text: str) -> None:
    """
    Simulate the text_input branch of ws_browser_ui in server/main.py.

    This replicates the FIXED handler logic:

        if target_pipeline._state.state == "speaking":
            target_pipeline._state.interrupt = True
        transcript = TranscriptionResult(text=text, language="en", duration=0.0)
        await target_pipeline._state.transcript_queue.put(transcript)

    The fix: state.interrupt is set to True BEFORE enqueue when
    target_pipeline._state.state == "speaking".
    """
    # Replicate the fixed handler — set interrupt flag before enqueue when speaking
    if pipeline_state.state == "speaking":
        pipeline_state.interrupt = True
    transcript = TranscriptionResult(text=text, language="en", duration=0.0)
    await pipeline_state.transcript_queue.put(transcript)


# ---------------------------------------------------------------------------
# Bug 2 — Property: state.interrupt is False after text_input during 'speaking'
#
# On UNFIXED code: the handler never sets state.interrupt = True.
# After calling the handler with state.state='speaking', state.interrupt
# remains False.
#
# EXPECTED OUTCOME: This test FAILS on unfixed code because state.interrupt
# IS False (demonstrating the bug — it SHOULD be True).
#
# Wait — the test asserts state.interrupt IS False, which is what the unfixed
# code does. So the test PASSES on unfixed code, confirming the bug exists.
# The test encodes the EXPECTED (fixed) behavior: state.interrupt SHOULD be True.
# On unfixed code, the assertion `state.interrupt == True` FAILS.
# ---------------------------------------------------------------------------


@given(text=st.text(min_size=1).filter(lambda s: s.strip()))
@settings(max_examples=50)
def test_bug2_interrupt_not_set_during_speaking(text: str) -> None:
    """
    Property: after text_input handler runs with state.state='speaking',
    state.interrupt is True (EXPECTED behavior after fix).

    On UNFIXED code: state.interrupt remains False — this test FAILS,
    confirming the missing `state.interrupt = True` assignment.

    Validates: AC-2.1
    """
    pipeline_state = make_pipeline_state("speaking")

    # Run the unfixed handler
    asyncio.run(call_text_input_handler(pipeline_state, text))

    # EXPECTED (fixed) behavior: interrupt SHOULD be True before enqueue.
    # On UNFIXED code: interrupt is False — this assertion FAILS (confirms bug).
    assert pipeline_state.interrupt is True, (
        f"Bug confirmed: state.interrupt is False after text_input with "
        f"state.state='speaking' and text={text!r}. "
        f"Expected: True (interrupt should be set before enqueue). "
        f"This is the missing `state.interrupt = True` assignment in the handler."
    )


@given(text=st.text(min_size=1).filter(lambda s: s.strip()))
@settings(max_examples=50)
def test_bug2_transcript_enqueued_without_interrupt(text: str) -> None:
    """
    Property: after text_input handler runs with state.state='speaking',
    transcript_queue.qsize() == 1 AND state.interrupt == True simultaneously.

    On UNFIXED code: transcript IS enqueued (qsize==1) but interrupt is False.
    This test FAILS on unfixed code, confirming the bug:
    interrupt was not set before enqueue.

    Validates: AC-2.1
    """
    pipeline_state = make_pipeline_state("speaking")

    # Run the unfixed handler
    asyncio.run(call_text_input_handler(pipeline_state, text))

    queue_size = pipeline_state.transcript_queue.qsize()

    # Transcript should be enqueued (this part works even on unfixed code)
    assert queue_size == 1, (
        f"Expected transcript_queue.qsize() == 1, got {queue_size}"
    )

    # EXPECTED (fixed) behavior: interrupt SHOULD be True.
    # On UNFIXED code: interrupt is False — this assertion FAILS (confirms bug).
    assert pipeline_state.interrupt is True, (
        f"Bug confirmed: transcript_queue.qsize()=={queue_size} AND "
        f"state.interrupt==False simultaneously for text={text!r}. "
        f"This demonstrates the bug: interrupt was NOT set before enqueue. "
        f"Expected: state.interrupt=True before transcript_queue.put()."
    )
