"""Unit tests for POST /api/tts — Requirements 3.1–3.8"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from server.tests.api.conftest import MINIMAL_WAV


class TestTTSEndpoint:
    @pytest.mark.parametrize("language", ["en", "ja", "zh"])
    def test_200_returns_wav_for_supported_languages(
        self, client: TestClient, language: str
    ) -> None:
        """HTTP 200 with audio/wav content-type for en/ja/zh."""
        client.app.state.tts_router.synthesize = AsyncMock(return_value=MINIMAL_WAV)
        resp = client.post("/api/tts", json={"text": "Hello", "language": language})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/wav")
        # Verify RIFF WAV header
        assert resp.content[:4] == b"RIFF"
        assert resp.content[8:12] == b"WAVE"

    def test_406_for_korean(self, client: TestClient) -> None:
        """HTTP 406 with fallback directive for language=ko (no synthesis call)."""
        client.app.state.tts_router.synthesize = AsyncMock(return_value=MINIMAL_WAV)
        resp = client.post("/api/tts", json={"text": "안녕하세요", "language": "ko"})
        assert resp.status_code == 406
        body = resp.json()
        assert body["fallback"] == "browser_tts"
        assert body["language"] == "ko"
        # synthesize must NOT have been called
        client.app.state.tts_router.synthesize.assert_not_called()

    def test_422_empty_text(self, client: TestClient) -> None:
        """HTTP 422 when text is empty string."""
        resp = client.post("/api/tts", json={"text": "", "language": "en"})
        assert resp.status_code == 422

    def test_422_whitespace_only_text(self, client: TestClient) -> None:
        """HTTP 422 when text is whitespace-only."""
        resp = client.post("/api/tts", json={"text": "   ", "language": "en"})
        assert resp.status_code == 422

    def test_422_missing_text(self, client: TestClient) -> None:
        """HTTP 422 when text field is absent."""
        resp = client.post("/api/tts", json={"language": "en"})
        assert resp.status_code == 422

    def test_422_unsupported_language(self, client: TestClient) -> None:
        """HTTP 422 for language not in accepted set."""
        resp = client.post("/api/tts", json={"text": "Hello", "language": "fr"})
        assert resp.status_code == 422

    def test_422_missing_language(self, client: TestClient) -> None:
        """HTTP 422 when language field is absent."""
        resp = client.post("/api/tts", json={"text": "Hello"})
        assert resp.status_code == 422

    def test_502_on_backend_exception(self, client: TestClient) -> None:
        """HTTP 502 when TTSRouter.synthesize() raises."""
        client.app.state.tts_router.synthesize = AsyncMock(
            side_effect=RuntimeError("Kokoro failed")
        )
        resp = client.post("/api/tts", json={"text": "Hello", "language": "en"})
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"] == "tts_failed"
