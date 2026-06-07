"""Unit tests for GET /api/health — Requirements 9.1–9.6"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:

    def test_200_fully_ready(self, client: TestClient) -> None:
        """HTTP 200 with all ready flags true when fully warmed up."""
        client.app.state.tts_warmed_up = True
        client.app.state.config.groq_api_key = "sk-test-key"
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["tts_ready"] is True
        assert data["stt_ready"] is True

    def test_tts_ready_false_when_not_warmed(self, client: TestClient) -> None:
        """tts_ready is False when warm-up has not completed."""
        client.app.state.tts_warmed_up = False
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["tts_ready"] is False

    def test_tts_ready_false_when_flag_absent(self, client: TestClient) -> None:
        """tts_ready is False when app.state.tts_warmed_up doesn't exist."""
        if hasattr(client.app.state, "tts_warmed_up"):
            del client.app.state.tts_warmed_up
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["tts_ready"] is False

    def test_stt_ready_false_when_no_api_key(self, client: TestClient) -> None:
        """stt_ready is False when groq_api_key is empty."""
        client.app.state.config.groq_api_key = ""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["stt_ready"] is False

    def test_no_request_body_required(self, client: TestClient) -> None:
        """Health endpoint needs no body."""
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_response_schema(self, client: TestClient) -> None:
        """Response must have status, tts_ready, and stt_ready keys."""
        resp = client.get("/api/health")
        data = resp.json()
        assert "status" in data
        assert "tts_ready" in data
        assert "stt_ready" in data
        assert isinstance(data["tts_ready"], bool)
        assert isinstance(data["stt_ready"], bool)
