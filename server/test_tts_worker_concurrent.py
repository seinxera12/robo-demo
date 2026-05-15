"""
Tests for the concurrent _tts_worker implementation (Task 5).

Sub-tasks:
  5.1  Property test — audio ordering preserved under concurrent synthesis
  5.2  Property test — concurrency cap never exceeds 2
  5.3  Unit test     — interrupt cancels all pending tasks, queue stays empty

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from server.pipeline import _END_OF_TOKENS, InterruptController, PipelineState, VoicePipeline
from server.llm.assembler import DeploymentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(session_id: str = "test-tts-session") -> PipelineState:
    """Create a minimal PipelineState with real asyncio queues."""
    return PipelineState(
        session_id=session_id,
        history=[],
        state="thinking",
        interrupt=False,
        detected_language="en",
        audio_queue=asyncio.Queue(),
        transcript_queue=asyncio.Queue(),
        token_queue=asyncio.Queue(),
        audio_out_queue=asyncio.Queue(),
        robo_active=False,
        deployment_config=DeploymentConfig.default(),
        model_tier="groq",
    )


def _make_pipeline(state: PipelineState) -> VoicePipeline:
    """
    Build a VoicePipeline with all heavy dependencies mocked out.
    Only the parts exercised by _tts_worker are wired up.
    """
    pipeline = VoicePipeline.__new__(VoicePipeline)
    pipeline._state = state
    pipeline._speech_end_time = time.monotonic() - 0.1
    pipeline._turn_audio_duration_ms = 0
    pipeline._tts_turn_complete = False
    pipeline._last_idle_drop_log = 0.0
    pipeline._token_count = 0
    pipeline._tasks = []

    # Stub out attributes not used by _tts_worker
    pipeline._audio_ws = MagicMock()
    pipeline._stt = MagicMock()
    pipeline._llm = MagicMock()
    pipeline._lang = MagicMock()
    pipeline._intent = MagicMock()
    pipeline._assembler = MagicMock()
    pipeline._router = MagicMock()
    pipeline._broadcast_fn = AsyncMock()
    pipeline._post_processor = MagicMock()
    pipeline._post_processor.clean = MagicMock(side_effect=lambda text, lang: text)

    # TTSRouter mock: accumulate returns None until we push a sentence boundary token
    # flush returns ""
    tts_router = MagicMock()
    tts_router.accumulate = MagicMock(return_value=None)
    tts_router.flush = MagicMock(return_value="")
    pipeline._tts = tts_router

    # InterruptController — required by _tts_worker
    pipeline._ic = InterruptController(state.session_id)

    return pipeline


async def _drain_queue(q: asyncio.Queue) -> list:
    """Drain all items currently in the queue and return them as a list."""
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


async def _run_tts_worker_with_sentences(
    pipeline: VoicePipeline,
    sentences: list[str],
    *,
    timeout: float = 5.0,
) -> None:
    """
    Feed sentences directly to _tts_worker by making tts.accumulate() return
    each sentence in order (one per token), then send _END_OF_TOKENS.

    The tts_router.accumulate mock is configured to return sentences[i] on
    the i-th call, then None for subsequent calls.  flush() returns "".
    """
    state = pipeline._state
    call_count = [0]

    def _accumulate_side_effect(token):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(sentences):
            return sentences[idx]
        return None

    pipeline._tts.accumulate.side_effect = _accumulate_side_effect
    pipeline._tts.flush.return_value = ""

    # Push one token per sentence + END_OF_TOKENS
    for i in range(len(sentences)):
        await state.token_queue.put(f"sentence_token_{i}")
    await state.token_queue.put(_END_OF_TOKENS)

    # Run _tts_worker until it sets _tts_turn_complete
    task = asyncio.create_task(pipeline._tts_worker())
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if pipeline._tts_turn_complete:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ===========================================================================
# 5.1  Property test — audio ordering preserved under concurrent synthesis
#
# **Property 5: Audio ordering preserved under concurrent synthesis**
# **Validates: Requirements 2.5**
# ===========================================================================


@given(
    n_sentences=st.integers(min_value=2, max_value=6),
)
@settings(max_examples=30, deadline=15_000)
def test_5_1_audio_ordering_preserved(n_sentences: int):
    """
    Property 5: Audio ordering preserved under concurrent synthesis.

    Mock _synthesize_and_enqueue to complete in reverse sentence order
    (last sentence finishes first via asyncio.sleep delays).  For any turn
    with N sentences (N ≥ 2), verify audio_out_queue receives WAV chunks in
    sentence order 0, 1, …, N-1.

    **Validates: Requirements 2.5**
    """

    async def _run():
        state = _make_state()
        pipeline = _make_pipeline(state)

        sentences = [f"Sentence {i}." for i in range(n_sentences)]

        # Each sentence produces a distinct WAV payload so we can identify order.
        # Synthesis delay is REVERSED: sentence 0 takes the longest, sentence N-1
        # finishes first — this would break ordering if tasks were not awaited in order.
        async def _mock_synthesize(sentence: str, sentence_index: int):
            # Reverse delay: last sentence finishes first
            delay = (n_sentences - sentence_index) * 0.01
            await asyncio.sleep(delay)
            # WAV payload encodes the sentence_index for identification
            wav = bytes([sentence_index]) * 44  # 44-byte fake WAV
            return (wav, sentence_index)

        pipeline._synthesize_and_enqueue = _mock_synthesize

        await _run_tts_worker_with_sentences(pipeline, sentences)

        wav_chunks = await _drain_queue(state.audio_out_queue)

        assert len(wav_chunks) == n_sentences, (
            f"Expected {n_sentences} WAV chunks, got {len(wav_chunks)}"
        )

        for expected_idx, chunk in enumerate(wav_chunks):
            # The first byte of our fake WAV encodes the sentence_index
            actual_idx = chunk[0]
            assert actual_idx == expected_idx, (
                f"WAV chunk at position {expected_idx} has sentence_index={actual_idx}, "
                f"expected {expected_idx}. Ordering was not preserved."
            )

    asyncio.get_event_loop().run_until_complete(_run())


# ===========================================================================
# 5.2  Property test — concurrency cap never exceeds 2
#
# **Property 6: Concurrency cap respected**
# **Validates: Requirements 2.2**
# ===========================================================================


@given(
    n_sentences=st.integers(min_value=3, max_value=8),
)
@settings(max_examples=20, deadline=15_000)
def test_5_2_concurrency_cap_never_exceeds_2(n_sentences: int):
    """
    Property 6: Concurrency cap respected.

    For any turn with 3+ sentences, the peak number of simultaneously
    in-flight synthesis tasks SHALL never exceed 2.

    **Validates: Requirements 2.2**
    """

    async def _run():
        state = _make_state()
        pipeline = _make_pipeline(state)

        sentences = [f"Sentence {i}." for i in range(n_sentences)]

        # Instrumentation: track concurrent in-flight count
        in_flight = [0]
        peak_in_flight = [0]

        async def _mock_synthesize(sentence: str, sentence_index: int):
            in_flight[0] += 1
            if in_flight[0] > peak_in_flight[0]:
                peak_in_flight[0] = in_flight[0]
            # Simulate synthesis taking some time so tasks overlap
            await asyncio.sleep(0.02)
            in_flight[0] -= 1
            wav = bytes([sentence_index]) * 44
            return (wav, sentence_index)

        pipeline._synthesize_and_enqueue = _mock_synthesize

        await _run_tts_worker_with_sentences(pipeline, sentences)

        assert peak_in_flight[0] <= 2, (
            f"Peak concurrent synthesis tasks was {peak_in_flight[0]}, "
            f"expected ≤ 2 (concurrency cap violated)"
        )

    asyncio.get_event_loop().run_until_complete(_run())


# ===========================================================================
# 5.3  Unit test — interrupt cancels all pending tasks, queue stays empty
#
# **Property 7: Interrupt cancels all pending synthesis**
# **Validates: Requirements 2.3, 2.4**
# ===========================================================================


@pytest.mark.asyncio
async def test_5_3_interrupt_cancels_pending_tasks_and_queue_empty():
    """
    Property 7: Interrupt cancels all pending synthesis.

    Set interrupt=True mid-turn; verify all pending tasks are cancelled and
    audio_out_queue remains empty.

    **Validates: Requirements 2.3, 2.4**
    """
    state = _make_state()
    pipeline = _make_pipeline(state)

    n_sentences = 4
    sentences = [f"Sentence {i}." for i in range(n_sentences)]

    # Track which tasks were cancelled
    cancelled_tasks: list[int] = []
    started_tasks: list[int] = []

    async def _mock_synthesize(sentence: str, sentence_index: int):
        started_tasks.append(sentence_index)
        try:
            # Long delay so tasks are still in-flight when interrupt fires
            await asyncio.sleep(10.0)
            wav = bytes([sentence_index]) * 44
            return (wav, sentence_index)
        except asyncio.CancelledError:
            cancelled_tasks.append(sentence_index)
            raise

    pipeline._synthesize_and_enqueue = _mock_synthesize

    # Configure accumulate to return sentences one by one
    call_count = [0]

    def _accumulate_side_effect(token):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(sentences):
            return sentences[idx]
        return None

    pipeline._tts.accumulate.side_effect = _accumulate_side_effect
    pipeline._tts.flush.return_value = ""

    # Push tokens for first 2 sentences (to get tasks in-flight), then set interrupt
    for i in range(2):
        await state.token_queue.put(f"sentence_token_{i}")

    # Start the worker
    worker_task = asyncio.create_task(pipeline._tts_worker())

    # Wait until at least one synthesis task has started
    deadline = asyncio.get_event_loop().time() + 3.0
    while asyncio.get_event_loop().time() < deadline:
        if len(started_tasks) >= 1:
            break
        await asyncio.sleep(0.01)

    # Set interrupt flag — this should cause the worker to cancel all pending tasks
    state.interrupt = True
    pipeline._ic.request_interrupt(source="test_interrupt")

    # Push a token to unblock the worker's token_queue.get() so it sees the interrupt
    await state.token_queue.put("interrupt_trigger_token")

    # Wait for the worker to process the interrupt
    deadline = asyncio.get_event_loop().time() + 3.0
    while asyncio.get_event_loop().time() < deadline:
        if not worker_task.done():
            await asyncio.sleep(0.01)
        else:
            break

    # Cancel the worker if it's still running (it loops forever)
    if not worker_task.done():
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    # Verify: audio_out_queue must be empty (no WAV bytes enqueued after interrupt)
    assert state.audio_out_queue.empty(), (
        f"audio_out_queue should be empty after interrupt, "
        f"but contains {state.audio_out_queue.qsize()} items"
    )

    # Verify: all started synthesis tasks were cancelled
    assert len(cancelled_tasks) > 0, (
        "Expected at least one synthesis task to be cancelled, but none were"
    )
    for idx in started_tasks:
        assert idx in cancelled_tasks, (
            f"Synthesis task {idx} was started but not cancelled"
        )
