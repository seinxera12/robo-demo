"""
Shared pytest fixtures for API endpoint unit tests.

Provides a FastAPI TestClient with all app.state dependencies mocked
so tests run without real LLM, TTS, or STT backends.
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import api_router
from server.api.navigate import NavigateSession, NavigateTurnResult
from server.models import TranscriptionResult


# ---------------------------------------------------------------------------
# Minimal WAV bytes fixture (valid RIFF header, 1 sample of silence)
# ---------------------------------------------------------------------------

def _make_minimal_wav() -> bytes:
    """Return a minimal valid 24 kHz mono WAV file with 1 silent sample."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00")  # one silent sample
    buf.seek(0)
    return buf.read()


MINIMAL_WAV = _make_minimal_wav()


# ---------------------------------------------------------------------------
# Mock component factories
# ---------------------------------------------------------------------------

def _make_mock_stt_backend(text="hello", language="en"):
    backend = MagicMock()
    backend.transcribe = AsyncMock(
        return_value=TranscriptionResult(text=text, language=language)
    )
    return backend


def _make_mock_tts_router(wav_bytes=None):
    router = MagicMock()
    router.synthesize = AsyncMock(return_value=wav_bytes or MINIMAL_WAV)
    return router


def _make_mock_intent_classifier(intent="general", language="en"):
    from server.llm.intent import IntentResult
    classifier = MagicMock()
    classifier.classify = AsyncMock(
        return_value=IntentResult(
            intent=intent,
            language=language,
            confidence=0.9,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="test",
            destination_query=None,
            accessibility_flag=False,
        )
    )
    return classifier


def _make_mock_config(groq_api_key="test-key", building_nav_origin="http://localhost:8001"):
    cfg = MagicMock()
    cfg.groq_api_key = groq_api_key
    cfg.building_nav_origin = building_nav_origin
    return cfg


def _make_mock_deployment_config(session_memory_turns=6):
    dc = MagicMock()
    dc.session_memory_turns = session_memory_turns
    dc.out_of_scope_response = {"en": "Out of scope."}
    dc.web_search_enabled = False
    return dc


# ---------------------------------------------------------------------------
# Core app fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_with_mocks():
    """Return a FastAPI app with all api endpoints and mocked app.state."""
    from fastapi.middleware.cors import CORSMiddleware

    application = FastAPI()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8001"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(api_router)

    # Populate app.state with mocks
    application.state.stt_backend = _make_mock_stt_backend()
    application.state.tts_router = _make_mock_tts_router()
    application.state.intent_classifier = _make_mock_intent_classifier()
    application.state.config = _make_mock_config()
    application.state.deployment_config = _make_mock_deployment_config()
    application.state.navigate_sessions = {}
    application.state.tts_warmed_up = True

    # Prompt assembler mock
    assembler = MagicMock()
    assembler.assemble_prompt = MagicMock(
        return_value=("system_prompt", [{"role": "user", "content": "test"}])
    )
    application.state.prompt_assembler = assembler

    # LLM chain mock — yields a single token
    async def _fake_stream(*args, **kwargs) -> AsyncIterator[str]:
        yield "Navigate to the cafeteria."

    llm_chain = MagicMock()
    llm_chain.stream = _fake_stream
    application.state.llm_chain = llm_chain

    # Router mock
    from server.llm.router import RouteResult
    router_mock = MagicMock()
    router_mock.route = AsyncMock(
        return_value=RouteResult(
            route_type="general",
            retrieved_context="",
            direct_response=None,
            clarification_suffix=None,
        )
    )
    application.state.router = router_mock

    # PostProcessor mock
    pp = MagicMock()
    pp.clean = MagicMock(side_effect=lambda text, lang: text)
    application.state.post_processor = pp

    return application


@pytest.fixture()
def client(app_with_mocks):
    """Return a TestClient for the mocked app."""
    with TestClient(app_with_mocks, raise_server_exceptions=True) as c:
        yield c
