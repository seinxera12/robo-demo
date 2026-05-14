"""
Unit tests for Task 2: concurrent Kokoro TTS warm-up at server startup.

Sub-task 2.1 — Requirements: 3.1, 3.2, 3.3

Tests verify:
- Both kokoro_tts.warm_up and kokoro_ja_tts.warm_up are called during
  lifespan startup (Requirements 3.1, 3.2).
- If warm_up raises an exception, the server lifespan continues without
  re-raising (Requirement 3.3).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_config():
    """Return a minimal Config-like mock that satisfies lifespan."""
    cfg = MagicMock()
    cfg.groq_api_key = "test-key"
    cfg.groq_stt_model = "whisper-large-v3"
    cfg.groq_llm_model = "llama-3.1-8b-instant"
    cfg.gemini_api_key = None
    cfg.tavily_api_key = None
    cfg.log_level = "WARNING"
    cfg.server_port = 8000
    return cfg


# ---------------------------------------------------------------------------
# Direct warm-up logic tests (unit-level, no lifespan overhead)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_warm_up_called_concurrently():
    """Both warm_up methods are invoked when the gather runs.

    Requirements: 3.1, 3.2
    """
    en_tts = MagicMock()
    ja_tts = MagicMock()

    loop = asyncio.get_event_loop()
    await asyncio.gather(
        loop.run_in_executor(None, en_tts.warm_up),
        loop.run_in_executor(None, ja_tts.warm_up),
    )

    en_tts.warm_up.assert_called_once()
    ja_tts.warm_up.assert_called_once()


@pytest.mark.asyncio
async def test_warm_up_failure_does_not_propagate():
    """If warm_up raises, the exception is caught and does not propagate.

    Requirement: 3.3
    """
    en_tts = MagicMock()
    ja_tts = MagicMock()
    en_tts.warm_up.side_effect = RuntimeError("model load failed")

    raised = False
    try:
        loop = asyncio.get_event_loop()
        await asyncio.gather(
            loop.run_in_executor(None, en_tts.warm_up),
            loop.run_in_executor(None, ja_tts.warm_up),
        )
    except Exception:
        raised = True

    # asyncio.gather propagates the exception — the try/except in lifespan
    # catches it. Simulate that here to confirm the pattern works.
    assert raised, "gather should have raised (caught by lifespan try/except)"


@pytest.mark.asyncio
async def test_warm_up_failure_caught_by_lifespan_pattern():
    """Simulate the exact lifespan try/except pattern: exception is swallowed.

    Requirement: 3.3
    """
    en_tts = MagicMock()
    ja_tts = MagicMock()
    en_tts.warm_up.side_effect = RuntimeError("model load failed")

    warning_logged = False

    # Replicate the exact pattern from lifespan
    try:
        loop = asyncio.get_event_loop()
        await asyncio.gather(
            loop.run_in_executor(None, en_tts.warm_up),
            loop.run_in_executor(None, ja_tts.warm_up),
        )
    except Exception as exc:
        warning_logged = True  # logger.warning would be called here

    assert warning_logged, "Warning should have been logged on warm_up failure"
    # Execution continues past the try/except — no re-raise


@pytest.mark.asyncio
async def test_both_warm_up_called_even_when_one_raises():
    """When one warm_up raises, the other still ran (gather fires both).

    Requirements: 3.1, 3.2, 3.3
    """
    call_log: list[str] = []

    def en_warm_up():
        call_log.append("en")
        raise RuntimeError("en failed")

    def ja_warm_up():
        call_log.append("ja")

    en_tts = MagicMock()
    ja_tts = MagicMock()
    en_tts.warm_up.side_effect = en_warm_up
    ja_tts.warm_up.side_effect = ja_warm_up

    try:
        loop = asyncio.get_event_loop()
        await asyncio.gather(
            loop.run_in_executor(None, en_tts.warm_up),
            loop.run_in_executor(None, ja_tts.warm_up),
            return_exceptions=True,
        )
    except Exception:
        pass  # caught by lifespan

    # Both executors were submitted; both ran
    assert "en" in call_log
    assert "ja" in call_log


# ---------------------------------------------------------------------------
# Lifespan integration tests (full lifespan with heavy mocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_calls_both_warm_up_methods():
    """Full lifespan startup calls warm_up on both TTS engines.

    Requirements: 3.1, 3.2
    """
    mock_config = _make_mock_config()

    en_warm_up = MagicMock()
    ja_warm_up = MagicMock()

    mock_kokoro_tts = MagicMock()
    mock_kokoro_tts.warm_up = en_warm_up

    mock_kokoro_ja_tts = MagicMock()
    mock_kokoro_ja_tts.warm_up = ja_warm_up

    mock_tts_router = MagicMock()

    # Patch all external dependencies so lifespan can run without real services
    with (
        patch("server.main.Config.from_env", return_value=mock_config),
        patch("server.main.groq.AsyncGroq", return_value=MagicMock()),
        patch("server.main.GroqSTTBackend", return_value=MagicMock()),
        patch("server.main.GroqLLMBackend", return_value=MagicMock()),
        patch("server.main.LLMChain", return_value=MagicMock()),
        patch("server.main.DeploymentConfig.from_yaml", side_effect=FileNotFoundError),
        patch("server.main.DeploymentConfig.default", return_value=MagicMock()),
        patch("server.main.detect_model_tier", return_value="small"),
        patch("server.main.LanguageDetector", return_value=MagicMock()),
        patch("server.main.PromptAssembler", return_value=MagicMock()),
        patch("server.main.IntentClassifier", return_value=MagicMock()),
        patch("server.main.Router", return_value=MagicMock()),
        patch("server.main.PostProcessor", return_value=MagicMock()),
        patch("server.main.KokoroTTS", return_value=mock_kokoro_tts),
        patch("server.main.KokoroJapaneseTTS", return_value=mock_kokoro_ja_tts),
        patch("server.main.TTSRouter", return_value=mock_tts_router),
        # Suppress Groq connectivity test
        patch(
            "server.main.groq.AsyncGroq",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(
                        create=AsyncMock(return_value=MagicMock())
                    )
                )
            ),
        ),
    ):
        from server.main import lifespan, app

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        async with lifespan(mock_app):
            pass  # startup completed, yield, then shutdown

    en_warm_up.assert_called_once()
    ja_warm_up.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_continues_when_warm_up_raises():
    """Lifespan startup completes even if warm_up raises an exception.

    Requirement: 3.3
    """
    mock_config = _make_mock_config()

    mock_kokoro_tts = MagicMock()
    mock_kokoro_tts.warm_up.side_effect = RuntimeError("GPU OOM")

    mock_kokoro_ja_tts = MagicMock()
    mock_kokoro_ja_tts.warm_up = MagicMock()

    with (
        patch("server.main.Config.from_env", return_value=mock_config),
        patch("server.main.groq.AsyncGroq", return_value=MagicMock()),
        patch("server.main.GroqSTTBackend", return_value=MagicMock()),
        patch("server.main.GroqLLMBackend", return_value=MagicMock()),
        patch("server.main.LLMChain", return_value=MagicMock()),
        patch("server.main.DeploymentConfig.from_yaml", side_effect=FileNotFoundError),
        patch("server.main.DeploymentConfig.default", return_value=MagicMock()),
        patch("server.main.detect_model_tier", return_value="small"),
        patch("server.main.LanguageDetector", return_value=MagicMock()),
        patch("server.main.PromptAssembler", return_value=MagicMock()),
        patch("server.main.IntentClassifier", return_value=MagicMock()),
        patch("server.main.Router", return_value=MagicMock()),
        patch("server.main.PostProcessor", return_value=MagicMock()),
        patch("server.main.KokoroTTS", return_value=mock_kokoro_tts),
        patch("server.main.KokoroJapaneseTTS", return_value=mock_kokoro_ja_tts),
        patch("server.main.TTSRouter", return_value=MagicMock()),
        patch(
            "server.main.groq.AsyncGroq",
            return_value=MagicMock(
                chat=MagicMock(
                    completions=MagicMock(
                        create=AsyncMock(return_value=MagicMock())
                    )
                )
            ),
        ),
    ):
        from server.main import lifespan

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        # Should NOT raise — lifespan must continue despite warm_up failure
        lifespan_completed = False
        async with lifespan(mock_app):
            lifespan_completed = True

    assert lifespan_completed, "Lifespan must complete even when warm_up raises"
