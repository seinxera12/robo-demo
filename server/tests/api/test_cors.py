"""Unit tests for CORS middleware — Requirements 1.1, 1.2, 1.3"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_ALLOWED_ORIGIN = "http://localhost:8001"
_DISALLOWED_ORIGIN = "http://evil.example.com"


class TestCORSMiddleware:

    def test_preflight_from_allowed_origin_returns_correct_headers(
        self, client: TestClient
    ) -> None:
        """OPTIONS preflight from allowed origin must return Access-Control-Allow-Origin."""
        resp = client.options(
            "/api/health",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        # FastAPI CORSMiddleware returns 200 for preflight
        assert resp.status_code in (200, 204)
        assert resp.headers.get("access-control-allow-origin") == _ALLOWED_ORIGIN

    def test_preflight_allows_get_post_options(self, client: TestClient) -> None:
        """Preflight response must list GET, POST, OPTIONS in allowed methods."""
        resp = client.options(
            "/api/stt",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed = resp.headers.get("access-control-allow-methods", "").upper()
        for method in ("GET", "POST", "OPTIONS"):
            assert method in allowed, (
                f"{method} missing from Access-Control-Allow-Methods: {allowed}"
            )

    def test_actual_request_from_allowed_origin_has_cors_header(
        self, client: TestClient
    ) -> None:
        """Regular GET from allowed origin must get Access-Control-Allow-Origin header."""
        resp = client.get("/api/health", headers={"Origin": _ALLOWED_ORIGIN})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == _ALLOWED_ORIGIN

    def test_request_from_disallowed_origin_does_not_reflect_origin(
        self, client: TestClient
    ) -> None:
        """Request from a non-allowed origin must NOT get Access-Control-Allow-Origin reflecting that origin."""
        resp = client.get("/api/health", headers={"Origin": _DISALLOWED_ORIGIN})
        # The server must NOT echo back the disallowed origin
        acao = resp.headers.get("access-control-allow-origin", "")
        assert acao != _DISALLOWED_ORIGIN, (
            f"Server should not allow origin {_DISALLOWED_ORIGIN!r}"
        )

    def test_no_origin_header_still_returns_200(self, client: TestClient) -> None:
        """Requests without Origin header (e.g. direct API calls) must still work."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
