"""Unit tests for POST /api/detect-language — Requirements 4.1–4.7"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestDetectLanguageEndpoint:
    @pytest.mark.parametrize("text,expected", [
        ("hello world", "en"),
        ("This is English.", "en"),
        ("abc123", "en"),
    ])
    def test_latin_text_returns_en(self, client: TestClient, text: str, expected: str) -> None:
        resp = client.post("/api/detect-language", json={"text": text})
        assert resp.status_code == 200
        assert resp.json()["language"] == expected

    @pytest.mark.parametrize("text", [
        "こんにちは",          # hiragana
        "カタカナ",             # katakana
        "日本語とhiraganaが混じる",  # mixed CJK + hiragana
    ])
    def test_japanese_text_returns_ja(self, client: TestClient, text: str) -> None:
        resp = client.post("/api/detect-language", json={"text": text})
        assert resp.status_code == 200
        assert resp.json()["language"] == "ja"

    @pytest.mark.parametrize("text", [
        "你好世界",      # Mandarin CJK only
        "北京上海",      # CJK ideographs only
    ])
    def test_chinese_text_returns_zh(self, client: TestClient, text: str) -> None:
        resp = client.post("/api/detect-language", json={"text": text})
        assert resp.status_code == 200
        assert resp.json()["language"] == "zh"

    @pytest.mark.parametrize("text", [
        "안녕하세요",    # Hangul syllables
        "한국어",        # Hangul
    ])
    def test_korean_text_returns_ko(self, client: TestClient, text: str) -> None:
        resp = client.post("/api/detect-language", json={"text": text})
        assert resp.status_code == 200
        assert resp.json()["language"] == "ko"

    def test_mixed_kana_and_cjk_returns_ja(self, client: TestClient) -> None:
        """Kana + CJK ideographs → ja (kana takes priority over CJK)."""
        resp = client.post("/api/detect-language", json={"text": "日本語の文章です"})
        assert resp.status_code == 200
        assert resp.json()["language"] == "ja"

    def test_422_empty_text(self, client: TestClient) -> None:
        resp = client.post("/api/detect-language", json={"text": ""})
        assert resp.status_code == 422

    def test_422_missing_text_field(self, client: TestClient) -> None:
        resp = client.post("/api/detect-language", json={})
        assert resp.status_code == 422

    def test_422_whitespace_only(self, client: TestClient) -> None:
        resp = client.post("/api/detect-language", json={"text": "   "})
        assert resp.status_code == 422

    def test_response_schema(self, client: TestClient) -> None:
        """Response must have exactly a 'language' key."""
        resp = client.post("/api/detect-language", json={"text": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "language" in data
        assert data["language"] in {"en", "ja", "zh", "ko"}
