"""Unit tests for POST /api/navigate — Requirements 5.1–5.12"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.api.navigate import ClassifyError, LLMError, NavigateSession, NavigateTurnResult

_VALID_BODY = {
    "text": "Where is the cafeteria?",
    "language": "en",
    "session_id": str(uuid.uuid4()),
    "building_context": {
        "current_node_label": "Main Lobby",
        "available_pois": ["Cafeteria", "Elevator"],
        "floor_name": "Ground Floor",
    },
}

_FIXED_TURN_RESULT = NavigateTurnResult(
    intent="navigation",
    destination_query="cafeteria",
    accessibility_flag=False,
    response_text="The cafeteria is on the ground floor.",
    needs_clarification=False,
    language="en",
)


class TestNavigateEndpoint:

    def test_200_all_seven_fields_present(self, client: TestClient) -> None:
        """HTTP 200 response must contain all seven required fields with correct types."""
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=_FIXED_TURN_RESULT)):
            resp = client.post("/api/navigate", json=_VALID_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["intent"], str)
        assert data["destination_query"] is None or isinstance(data["destination_query"], str)
        assert isinstance(data["accessibility_flag"], bool)
        assert isinstance(data["response_text"], str)
        assert isinstance(data["needs_clarification"], bool)
        assert isinstance(data["language"], str)
        assert isinstance(data["session_id"], str)

    def test_session_id_echoed(self, client: TestClient) -> None:
        """session_id in response must equal session_id in request."""
        sid = str(uuid.uuid4())
        body = {**_VALID_BODY, "session_id": sid}
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=_FIXED_TURN_RESULT)):
            resp = client.post("/api/navigate", json=body)
        assert resp.status_code == 200
        assert resp.json()["session_id"] == sid

    def test_new_session_created(self, client: TestClient) -> None:
        """A new session_id not in the store creates a new NavigateSession."""
        sid = str(uuid.uuid4())
        body = {**_VALID_BODY, "session_id": sid}
        assert sid not in client.app.state.navigate_sessions
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=_FIXED_TURN_RESULT)):
            resp = client.post("/api/navigate", json=body)
        assert resp.status_code == 200
        assert sid in client.app.state.navigate_sessions

    def test_existing_session_reused_and_last_active_updated(self, client: TestClient) -> None:
        """An existing session_id reuses the session and updates last_active."""
        sid = str(uuid.uuid4())
        old_time = datetime.utcnow() - timedelta(minutes=10)
        existing = NavigateSession(history=[], last_active=old_time)
        client.app.state.navigate_sessions[sid] = existing

        body = {**_VALID_BODY, "session_id": sid}
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=_FIXED_TURN_RESULT)):
            resp = client.post("/api/navigate", json=body)
        assert resp.status_code == 200
        session = client.app.state.navigate_sessions[sid]
        assert session.last_active > old_time

    def test_history_grows_and_is_trimmed(self, client: TestClient) -> None:
        """After N calls with same session_id, history stays within session_memory_turns*2."""
        sid = str(uuid.uuid4())
        memory_turns = client.app.state.deployment_config.session_memory_turns  # 6
        body = {**_VALID_BODY, "session_id": sid}

        # Make 10 calls — well over the 6-turn limit
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=_FIXED_TURN_RESULT)):
            for _ in range(10):
                resp = client.post("/api/navigate", json=body)
                assert resp.status_code == 200

        session = client.app.state.navigate_sessions[sid]
        assert len(session.history) <= memory_turns * 2

    def test_422_empty_text(self, client: TestClient) -> None:
        body = {**_VALID_BODY, "text": ""}
        resp = client.post("/api/navigate", json=body)
        assert resp.status_code == 422

    def test_422_whitespace_text(self, client: TestClient) -> None:
        body = {**_VALID_BODY, "text": "   "}
        resp = client.post("/api/navigate", json=body)
        assert resp.status_code == 422

    def test_422_missing_required_field(self, client: TestClient) -> None:
        """Missing building_context → 422."""
        body = {k: v for k, v in _VALID_BODY.items() if k != "building_context"}
        resp = client.post("/api/navigate", json=body)
        assert resp.status_code == 422

    def test_502_on_classify_error(self, client: TestClient) -> None:
        with patch(
            "server.api.navigate.run_navigate_turn",
            new=AsyncMock(side_effect=ClassifyError("LLM down")),
        ):
            resp = client.post("/api/navigate", json=_VALID_BODY)
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"] == "classify_failed"

    def test_502_on_llm_error(self, client: TestClient) -> None:
        with patch(
            "server.api.navigate.run_navigate_turn",
            new=AsyncMock(side_effect=LLMError("stream failed")),
        ):
            resp = client.post("/api/navigate", json=_VALID_BODY)
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"] == "llm_failed"

    def test_destination_query_in_response(self, client: TestClient) -> None:
        """destination_query from NavigateTurnResult is returned correctly."""
        result = NavigateTurnResult(
            intent="navigation",
            destination_query="cafeteria",
            accessibility_flag=False,
            response_text="Head to the cafeteria.",
            needs_clarification=False,
            language="en",
        )
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=result)):
            resp = client.post("/api/navigate", json=_VALID_BODY)
        assert resp.status_code == 200
        assert resp.json()["destination_query"] == "cafeteria"

    def test_accessibility_flag_true_in_response(self, client: TestClient) -> None:
        result = NavigateTurnResult(
            intent="navigation",
            destination_query="elevator",
            accessibility_flag=True,
            response_text="Accessible route to elevator.",
            needs_clarification=False,
            language="en",
        )
        with patch("server.api.navigate.run_navigate_turn", new=AsyncMock(return_value=result)):
            resp = client.post("/api/navigate", json=_VALID_BODY)
        assert resp.status_code == 200
        assert resp.json()["accessibility_flag"] is True
