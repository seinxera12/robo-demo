"""Unit tests for POST /api/stt — Requirements 2.1–2.7"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server.models import TranscriptionResult


class TestSTTEndpoint:
    def test_200_returns_text_and_language(self, client: TestClient) -> None:
        """HTTP 200 with correct text and language fields."""
        client.app.state.stt_backend.transcribe = AsyncMock(
            return_value=TranscriptionResult(text="hello world", language="en")
        )
        resp = client.post(
            "/api/stt",
            files={"file": ("audio.wav", b"\x00\x01\x02\x03", "audio/wav")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "hello world"
        assert data["language"] == "en"

    def test_passthrough_fidelity(self, client: TestClient) -> None:
        """Response text/language must match exactly what transcribe() returned."""
        client.app.state.stt_backend.transcribe = AsyncMock(
            return_value=TranscriptionResult(text="こんにちは", language="ja")
        )
        resp = client.post(
            "/api/stt",
            files={"file": ("audio.wav", b"\xff\xfe", "audio/wav")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "こんにちは"
        assert data["language"] == "ja"

    def test_422_missing_file_field(self, client: TestClient) -> None:
        """HTTP 422 when the file field is absent."""
        resp = client.post("/api/stt")
        assert resp.status_code == 422

    def test_422_empty_file(self, client: TestClient) -> None:
        """HTTP 422 when uploaded file is zero bytes."""
        resp = client.post(
            "/api/stt",
            files={"file": ("audio.wav", b"", "audio/wav")},
        )
        assert resp.status_code == 422

    def test_502_on_backend_exception(self, client: TestClient) -> None:
        """HTTP 502 when GroqSTTBackend.transcribe() raises."""
        client.app.state.stt_backend.transcribe = AsyncMock(
            side_effect=RuntimeError("Groq API error")
        )
        resp = client.post(
            "/api/stt",
            files={"file": ("audio.wav", b"\x00\x01", "audio/wav")},
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "stt_failed"
