"""
Tests for _llm_worker Call 2 streaming behaviour (Task 7).

Covers:
  7.1  Property test — token ordering preserved end-to-end
  7.2  Property test — PostProcessor.clean NOT applied to token_queue
  7.3  Unit tests   — retry and fallback paths
  7.4  Unit test    — clarification suffix pushed before END_OF_TOKENS
  7.5  Unit test    — interrupt mid-stream stops token delivery

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7**
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers — lightweight stand-ins for the real pipeline objects
# ---------------------------------------------------------------------------

# Import the sentinel from the real module so tests use the exact same object.
from server.pipeline import _END_OF_TOKENS, PipelineState, VoicePipeline


def _make_state(session_id: str = "test-session-1234") -> PipelineState:
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
    )


async def _drain_queue(q: asyncio.Queue) -> list:
    """Drain all items currently in the queue and return them as a list."""
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


def _make_pipeline(state: PipelineState, llm_stream_mock, post_processor_mock=None) -> VoicePipeline:
    """
    Build a VoicePipeline with all heavy dependencies mocked out.

    Only the parts exercised by _llm_worker are wired up; everything else
    is a no-op MagicMock.
    """
    # LLM chain mock
    llm_chain = MagicMock()
    llm_chain.stream = llm_stream_mock

    # PostProcessor mock (default: identity)
    if post_processor_mock is None:
        pp = MagicMock()
        pp.clean = MagicMock(side_effect=lambda text, lang: text)
    else:
        pp = post_processor_mock

    # Intent classifier — returns a general intent with high confidence
    intent_result = MagicMock()
    intent_result.intent = "general"
    intent_result.language = "en"
    intent_result.confidence = 0.9
    intent_result.needs_clarification = False
    intent_result.clarification_reason = None
    intent_result.query_clean = "test query"

    intent_classifier = MagicMock()
    intent_classifier.classify = AsyncMock(return_value=intent_result)

    # Router — returns a general route (no direct_response, no suffix by default)
    route_result = MagicMock()
    route_result.route_type = "general"
    route_result.direct_response = None
    route_result.retrieved_context = ""
    route_result.clarification_suffix = None

    router = MagicMock()
    router.route = AsyncMock(return_value=route_result)

    # PromptAssembler — returns minimal messages list
    assembler = MagicMock()
    assembler.assemble_prompt = MagicMock(
        return_value=("system prompt", [{"role": "user", "content": "test"}])
    )

    # Broadcast function — no-op
    async def _noop_broadcast(msg):
        pass

    pipeline = VoicePipeline(
        audio_client_ws=MagicMock(),
        state=state,
        stt_backend=MagicMock(),
        llm_chain=llm_chain,
        tts_router=MagicMock(),
        lang_detector=MagicMock(),
        intent_classifier=intent_classifier,
        prompt_assembler=assembler,
        router=router,
        post_processor=pp,
        broadcast_fn=_noop_broadcast,
    )
    return pipeline, route_result


async def _run_one_turn(tokens: list[str], state: PipelineState, pipeline: VoicePipeline) -> list:
    """
    Push one transcript into transcript_queue, run _llm_worker for one turn,
    then return all items drained from token_queue.
    """
    from server.models import TranscriptionResult

    transcript = TranscriptionResult(text="hello world", language="en")
    await state.transcript_queue.put(transcript)

    # Run _llm_worker; it loops forever so we cancel after the first turn.
    task = asyncio.create_task(pipeline._llm_worker())
    # Wait until token_queue has at least one item (or timeout)
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if not state.token_queue.empty():
            # Give the worker a moment to push END_OF_TOKENS too
            await asyncio.sleep(0.05)
            break
        await asyncio.sleep(0.01)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    return await _drain_queue(state.token_queue)


# ---------------------------------------------------------------------------
# Async generator helpers
# ---------------------------------------------------------------------------

async def _tokens_gen(tokens: list[str]) -> AsyncIterator[str]:
    """Yield tokens one by one."""
    for t in tokens:
        yield t


async def _empty_gen() -> AsyncIterator[str]:
    """Yield nothing (empty stream)."""
    return
    yield  # make it an async generator


# ===========================================================================
# 7.1  Property test — token ordering preserved end-to-end
#
# **Property 1: Token ordering preserved end-to-end**
# **Validates: Requirements 1.1, 1.3**
# ===========================================================================

@given(
    tokens=st.lists(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Nd"),
                whitelist_characters=" ",
            ),
            min_size=1,
            max_size=20,
        ),
        min_size=1,
        max_size=15,
    )
)
@settings(max_examples=50, deadline=10_000)
def test_7_1_token_ordering_preserved(tokens):
    """
    Property 1: Token ordering preserved end-to-end.

    For any sequence of N tokens produced by the LLM stream, the tokens
    pushed to token_queue SHALL appear in the same order, followed by
    END_OF_TOKENS as the final item.

    **Validates: Requirements 1.1, 1.3**
    """

    async def _run():
        state = _make_state()

        call_count = [0]

        def _stream_mock(messages, max_tokens=None, temperature=None):
            call_count[0] += 1
            return _tokens_gen(tokens)

        pipeline, _ = _make_pipeline(state, _stream_mock)
        queue_items = await _run_one_turn(tokens, state, pipeline)

        # All tokens must appear in order
        assert len(queue_items) >= len(tokens) + 1, (
            f"Expected at least {len(tokens) + 1} items, got {len(queue_items)}"
        )
        for i, expected in enumerate(tokens):
            assert queue_items[i] == expected, (
                f"Token at position {i}: expected {expected!r}, got {queue_items[i]!r}"
            )
        # END_OF_TOKENS must be the last item
        assert queue_items[-1] is _END_OF_TOKENS, (
            f"Last item should be _END_OF_TOKENS, got {queue_items[-1]!r}"
        )

    asyncio.get_event_loop().run_until_complete(_run())


# ===========================================================================
# 7.2  Property test — PostProcessor NOT applied to token_queue
#
# **Property 3: PostProcessor.clean does not affect token_queue contents**
# **Validates: Requirements 1.6**
# ===========================================================================

_SENTINEL_CLEANED = "<<<CLEANED_SENTINEL>>>"


@given(
    tokens=st.lists(
        st.text(min_size=1, max_size=10).filter(
            lambda t: _SENTINEL_CLEANED not in t and t.strip() != ""
        ),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=50, deadline=10_000)
def test_7_2_postprocessor_not_applied_to_token_queue(tokens):
    """
    Property 3: PostProcessor.clean does not affect token_queue contents.

    The sentinel string returned by PostProcessor.clean SHALL NOT appear in
    token_queue.  The raw tokens SHALL appear unchanged.  The sentinel SHALL
    appear in the llm_text_chunk broadcast.

    **Validates: Requirements 1.6**
    """

    async def _run():
        state = _make_state()
        broadcast_messages: list[dict] = []

        # PostProcessor that always returns the sentinel
        pp = MagicMock()
        pp.clean = MagicMock(return_value=_SENTINEL_CLEANED)

        def _stream_mock(messages, max_tokens=None, temperature=None):
            return _tokens_gen(tokens)

        pipeline, _ = _make_pipeline(state, _stream_mock, post_processor_mock=pp)

        # Capture broadcasts
        async def _capture_broadcast(msg):
            broadcast_messages.append(msg)

        pipeline._broadcast_fn = _capture_broadcast

        queue_items = await _run_one_turn(tokens, state, pipeline)

        # Sentinel must NOT appear in token_queue
        for item in queue_items:
            if item is not _END_OF_TOKENS:
                assert item != _SENTINEL_CLEANED, (
                    "PostProcessor sentinel found in token_queue — "
                    "PostProcessor.clean must not be applied to individual tokens"
                )

        # Raw tokens must appear in token_queue (in order)
        non_sentinel_items = [i for i in queue_items if i is not _END_OF_TOKENS]
        assert non_sentinel_items == tokens, (
            f"Raw tokens in queue {non_sentinel_items!r} != expected {tokens!r}"
        )

        # Sentinel MUST appear in at least one llm_text_chunk broadcast
        text_chunks = [
            m["text"] for m in broadcast_messages if m.get("type") == "llm_text_chunk"
        ]
        assert any(_SENTINEL_CLEANED in chunk for chunk in text_chunks), (
            "PostProcessor sentinel not found in any llm_text_chunk broadcast"
        )

    asyncio.get_event_loop().run_until_complete(_run())


# ===========================================================================
# 7.3  Unit tests — retry and fallback paths
#
# **Property 2: Retry tokens reach token_queue**
# **Validates: Requirements 1.4, 1.5**
# ===========================================================================

@pytest.mark.asyncio
async def test_7_3a_fallback_after_two_empty_streams():
    """
    When the LLM stream returns empty twice, the fallback message SHALL be
    pushed to token_queue followed by END_OF_TOKENS.

    **Validates: Requirements 1.4, 1.5**
    """
    state = _make_state()
    call_count = [0]

    def _stream_mock(messages, max_tokens=None, temperature=None):
        call_count[0] += 1
        return _empty_gen()

    pipeline, _ = _make_pipeline(state, _stream_mock)
    queue_items = await _run_one_turn([], state, pipeline)

    _FALLBACK_MESSAGE = (
        "I'm having trouble connecting right now. Please try again in a moment."
    )

    assert len(queue_items) >= 2, f"Expected at least 2 items, got {queue_items!r}"
    assert queue_items[0] == _FALLBACK_MESSAGE, (
        f"Expected fallback message, got {queue_items[0]!r}"
    )
    assert queue_items[-1] is _END_OF_TOKENS, "Last item must be END_OF_TOKENS"
    # Stream should have been called twice (initial + retry)
    assert call_count[0] == 2, f"Expected 2 stream calls, got {call_count[0]}"


@pytest.mark.asyncio
async def test_7_3b_retry_tokens_reach_queue():
    """
    When the first stream attempt returns empty and the retry returns tokens,
    the retry tokens SHALL appear in token_queue before END_OF_TOKENS.

    **Validates: Requirements 1.4**
    """
    state = _make_state()
    retry_tokens = ["retry", " ", "token", " ", "here"]
    call_count = [0]

    def _stream_mock(messages, max_tokens=None, temperature=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _empty_gen()
        return _tokens_gen(retry_tokens)

    pipeline, _ = _make_pipeline(state, _stream_mock)
    queue_items = await _run_one_turn([], state, pipeline)

    # Retry tokens must appear in order
    non_sentinel = [i for i in queue_items if i is not _END_OF_TOKENS]
    assert non_sentinel == retry_tokens, (
        f"Retry tokens {non_sentinel!r} != expected {retry_tokens!r}"
    )
    assert queue_items[-1] is _END_OF_TOKENS
    assert call_count[0] == 2, f"Expected 2 stream calls, got {call_count[0]}"


# ===========================================================================
# 7.4  Unit test — clarification suffix pushed before END_OF_TOKENS
#
# **Validates: Requirements 1.7**
# ===========================================================================

@pytest.mark.asyncio
async def test_7_4_clarification_suffix_in_queue():
    """
    When route_result.clarification_suffix is set, the suffix token SHALL
    appear in token_queue before END_OF_TOKENS.

    **Validates: Requirements 1.7**
    """
    state = _make_state()
    base_tokens = ["Hello", " ", "world"]
    suffix = "some suffix"

    def _stream_mock(messages, max_tokens=None, temperature=None):
        return _tokens_gen(base_tokens)

    pipeline, route_result = _make_pipeline(state, _stream_mock)
    # Set the clarification suffix on the route result mock
    route_result.clarification_suffix = suffix

    queue_items = await _run_one_turn(base_tokens, state, pipeline)

    # Expected: base_tokens + " some suffix" + END_OF_TOKENS
    expected_suffix_token = " " + suffix
    assert _END_OF_TOKENS in queue_items, "END_OF_TOKENS must be in queue"
    eot_index = queue_items.index(_END_OF_TOKENS)

    # Suffix token must appear before END_OF_TOKENS
    assert expected_suffix_token in queue_items[:eot_index], (
        f"Suffix token {expected_suffix_token!r} not found before END_OF_TOKENS. "
        f"Queue: {queue_items!r}"
    )

    # Base tokens must appear before the suffix
    suffix_index = queue_items.index(expected_suffix_token)
    for i, tok in enumerate(base_tokens):
        assert queue_items[i] == tok, (
            f"Base token at position {i}: expected {tok!r}, got {queue_items[i]!r}"
        )
    assert suffix_index > len(base_tokens) - 1, (
        "Suffix must appear after all base tokens"
    )


# ===========================================================================
# 7.5  Unit test — interrupt mid-stream stops token delivery
#
# **Validates: Requirements 1.2**
# ===========================================================================

@pytest.mark.asyncio
async def test_7_5_interrupt_mid_stream():
    """
    When interrupt is set after the 3rd token in a 10-token stream, no tokens
    after the interrupt SHALL appear in token_queue, and END_OF_TOKENS SHALL
    still be pushed.

    **Validates: Requirements 1.2**
    """
    state = _make_state()
    all_tokens = [f"tok{i}" for i in range(10)]
    interrupt_after = 3  # set interrupt after 3rd token is yielded

    tokens_yielded = [0]

    async def _interruptible_gen(messages, max_tokens=None, temperature=None):
        for i, tok in enumerate(all_tokens):
            if i == interrupt_after:
                # Simulate barge-in: set interrupt flag before yielding this token
                state.interrupt = True
            yield tok
            tokens_yielded[0] = i + 1

    pipeline, _ = _make_pipeline(state, _interruptible_gen)
    queue_items = await _run_one_turn(all_tokens, state, pipeline)

    # END_OF_TOKENS must be present (worker must unblock tts_worker)
    assert _END_OF_TOKENS in queue_items, (
        "END_OF_TOKENS must be pushed even after interrupt"
    )

    # No tokens from position interrupt_after onward should appear
    # (the interrupt check fires before the token at interrupt_after is pushed)
    non_sentinel = [i for i in queue_items if i is not _END_OF_TOKENS]
    for tok in non_sentinel:
        idx = int(tok[3:])  # "tok3" → 3
        assert idx < interrupt_after, (
            f"Token {tok!r} (index {idx}) appeared after interrupt at {interrupt_after}"
        )
