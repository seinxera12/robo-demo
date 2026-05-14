"""
Unit tests for VoicePipeline._synthesize_and_enqueue.

Task 4.1 — Validates: Requirements 1.6, 4.1, 4.3

**Property 4: Per-sentence cleaning round-trip**
**Validates: Requirements 1.6, 4.1, 4.3**
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so we can instantiate VoicePipeline without real dependencies
# ---------------------------------------------------------------------------


def _make_pipeline_state(session_id: str = "test-session-1234") -> object:
    """Return a minimal PipelineState-like object."""
    from server.pipeline import PipelineState
    from server.llm.assembler import DeploymentConfig

    return PipelineState(
        session_id=session_id,
        history=[],
        state="listening",
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


def _make_voice_pipeline(state, tts_mock, post_processor_mock):
    """Construct a VoicePipeline with all heavy dependencies mocked out."""
    from server.pipeline import VoicePipeline

    pipeline = VoicePipeline.__new__(VoicePipeline)
    pipeline._state = state
    pipeline._tts = tts_mock
    pipeline._post_processor = post_processor_mock
    pipeline._speech_end_time = time.monotonic() - 0.5  # 500 ms ago
    pipeline._turn_audio_duration_ms = 0
    pipeline._tts_turn_complete = False

    # Stub out other attributes that _synthesize_and_enqueue doesn't use
    pipeline._audio_ws = MagicMock()
    pipeline._stt = MagicMock()
    pipeline._llm = MagicMock()
    pipeline._lang = MagicMock()
    pipeline._intent = MagicMock()
    pipeline._assembler = MagicMock()
    pipeline._router = MagicMock()
    pipeline._broadcast_fn = AsyncMock()
    pipeline._tasks = []
    pipeline._token_count = 0
    pipeline._last_idle_drop_log = 0.0

    return pipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tts_mock():
    mock = MagicMock()
    mock.synthesize = AsyncMock(return_value=b"\x00" * 100)
    return mock


@pytest.fixture
def post_processor_mock():
    mock = MagicMock()
    # Default: return the sentence unchanged
    mock.clean = MagicMock(side_effect=lambda text, lang: text)
    return mock


@pytest.fixture
def state():
    return _make_pipeline_state()


@pytest.fixture
def pipeline(state, tts_mock, post_processor_mock):
    return _make_voice_pipeline(state, tts_mock, post_processor_mock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_called_with_raw_sentence_and_detected_language(
    pipeline, tts_mock, post_processor_mock, state
):
    """Verify PostProcessor.clean is called with the raw sentence and detected_language."""
    state.detected_language = "en"
    sentence = "Hello, world."

    await pipeline._synthesize_and_enqueue(sentence, sentence_index=0)

    post_processor_mock.clean.assert_called_once_with(sentence, "en")


@pytest.mark.asyncio
async def test_clean_called_before_synthesize(
    pipeline, tts_mock, post_processor_mock
):
    """Verify clean is called before synthesize (clean output is passed to synthesize)."""
    cleaned = "Hello world"
    # Override side_effect so return_value takes effect
    post_processor_mock.clean.side_effect = None
    post_processor_mock.clean.return_value = cleaned

    await pipeline._synthesize_and_enqueue("Hello, world.", sentence_index=1)

    # synthesize should receive the cleaned sentence
    tts_mock.synthesize.assert_called_once_with(cleaned, pipeline._state.detected_language)


@pytest.mark.asyncio
async def test_returns_wav_bytes_and_sentence_index_on_success(
    pipeline, tts_mock, post_processor_mock
):
    """Verify return value is (wav_bytes, sentence_index) on success."""
    wav = b"\x52\x49\x46\x46" + b"\x00" * 96  # fake WAV header + body
    tts_mock.synthesize.return_value = wav

    result = await pipeline._synthesize_and_enqueue("Hello.", sentence_index=2)

    assert result is not None
    wav_bytes, idx = result
    assert wav_bytes == wav
    assert idx == 2


@pytest.mark.asyncio
async def test_returns_none_when_clean_returns_empty_string(
    pipeline, tts_mock, post_processor_mock
):
    """Verify return value is None when PostProcessor.clean returns empty string."""
    # Override side_effect so return_value takes effect
    post_processor_mock.clean.side_effect = None
    post_processor_mock.clean.return_value = ""

    result = await pipeline._synthesize_and_enqueue("**bold text**", sentence_index=0)

    assert result is None
    # synthesize should NOT be called when clean returns empty
    tts_mock.synthesize.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_when_synthesize_returns_empty_bytes(
    pipeline, tts_mock, post_processor_mock
):
    """Verify return value is None when synthesize returns empty bytes."""
    tts_mock.synthesize.return_value = b""

    result = await pipeline._synthesize_and_enqueue("Hello.", sentence_index=0)

    assert result is None


@pytest.mark.asyncio
async def test_ttfs_ms_logged_only_for_sentence_index_zero(
    pipeline, tts_mock, post_processor_mock
):
    """Verify ttfs_ms is logged only when sentence_index == 0."""
    with patch("server.pipeline.pipeline_event") as mock_event:
        # sentence_index = 0 → should log first_sentence_synthesis_start
        await pipeline._synthesize_and_enqueue("First sentence.", sentence_index=0)

        first_sentence_calls = [
            c for c in mock_event.call_args_list
            if c.args[1] == "first_sentence_synthesis_start"
        ]
        assert len(first_sentence_calls) == 1, (
            "Expected exactly one 'first_sentence_synthesis_start' event for sentence_index=0"
        )

        mock_event.reset_mock()

        # sentence_index = 1 → should NOT log first_sentence_synthesis_start
        await pipeline._synthesize_and_enqueue("Second sentence.", sentence_index=1)

        first_sentence_calls_after = [
            c for c in mock_event.call_args_list
            if c.args[1] == "first_sentence_synthesis_start"
        ]
        assert len(first_sentence_calls_after) == 0, (
            "Expected no 'first_sentence_synthesis_start' event for sentence_index=1"
        )


@pytest.mark.asyncio
async def test_ttfs_ms_not_logged_for_non_zero_sentence_index(
    pipeline, tts_mock, post_processor_mock
):
    """Verify ttfs_ms is NOT logged for sentence_index > 0."""
    with patch("server.pipeline.pipeline_event") as mock_event:
        for idx in [1, 2, 5, 10]:
            mock_event.reset_mock()
            await pipeline._synthesize_and_enqueue(f"Sentence {idx}.", sentence_index=idx)

            first_sentence_calls = [
                c for c in mock_event.call_args_list
                if c.args[1] == "first_sentence_synthesis_start"
            ]
            assert len(first_sentence_calls) == 0, (
                f"Expected no 'first_sentence_synthesis_start' for sentence_index={idx}"
            )


@pytest.mark.asyncio
async def test_synthesised_event_logged_on_success(
    pipeline, tts_mock, post_processor_mock
):
    """Verify 'synthesised' pipeline_event is logged after successful synthesis."""
    wav = b"\x00" * 200
    tts_mock.synthesize.return_value = wav

    with patch("server.pipeline.pipeline_event") as mock_event:
        await pipeline._synthesize_and_enqueue("Hello.", sentence_index=3)

        synthesised_calls = [
            c for c in mock_event.call_args_list
            if c.args[1] == "synthesised"
        ]
        assert len(synthesised_calls) == 1

        kwargs = synthesised_calls[0].kwargs
        assert kwargs["sentence_index"] == 3
        assert kwargs["wav_kb"] == len(wav) // 1024
        assert "synth_ms" in kwargs


@pytest.mark.asyncio
async def test_does_not_put_to_audio_out_queue(
    pipeline, tts_mock, post_processor_mock, state
):
    """Verify _synthesize_and_enqueue does NOT put to audio_out_queue (caller's responsibility)."""
    tts_mock.synthesize.return_value = b"\x00" * 100

    await pipeline._synthesize_and_enqueue("Hello.", sentence_index=0)

    assert state.audio_out_queue.empty(), (
        "_synthesize_and_enqueue must not put to audio_out_queue directly"
    )


@pytest.mark.asyncio
async def test_returns_none_on_synthesis_exception(
    pipeline, tts_mock, post_processor_mock
):
    """Verify return value is None when synthesize raises an exception."""
    tts_mock.synthesize.side_effect = RuntimeError("TTS engine crashed")

    result = await pipeline._synthesize_and_enqueue("Hello.", sentence_index=0)

    assert result is None


@pytest.mark.asyncio
async def test_sentence_index_preserved_in_return_value(
    pipeline, tts_mock, post_processor_mock
):
    """Verify the sentence_index in the return tuple matches the input."""
    tts_mock.synthesize.return_value = b"\x00" * 50

    for idx in [0, 1, 2, 7, 99]:
        result = await pipeline._synthesize_and_enqueue(f"Sentence {idx}.", sentence_index=idx)
        assert result is not None
        _, returned_idx = result
        assert returned_idx == idx, f"Expected sentence_index={idx}, got {returned_idx}"


@pytest.mark.asyncio
async def test_japanese_language_passed_to_clean_and_synthesize(
    pipeline, tts_mock, post_processor_mock, state
):
    """Verify detected_language='ja' is passed through to clean and synthesize."""
    state.detected_language = "ja"
    sentence = "こんにちは。"
    cleaned = "こんにちは。"
    post_processor_mock.clean.return_value = cleaned

    result = await pipeline._synthesize_and_enqueue(sentence, sentence_index=0)

    post_processor_mock.clean.assert_called_once_with(sentence, "ja")
    tts_mock.synthesize.assert_called_once_with(cleaned, "ja")
    assert result is not None
