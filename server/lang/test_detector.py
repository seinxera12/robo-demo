"""
Unit tests for server.lang.detector.LanguageDetector.

Covers requirements 4.1–4.4:
  4.1  "en" → "en"
  4.2  "ja" → "ja"
  4.3  unsupported codes (e.g. "fr") → "en" (default)
  4.4  empty string → "en" (default)
"""

import pytest

from server.models import TranscriptionResult
from server.lang.detector import LanguageDetector


@pytest.fixture
def detector() -> LanguageDetector:
    return LanguageDetector()


def _result(language: str) -> TranscriptionResult:
    """Helper: build a TranscriptionResult with the given language code."""
    return TranscriptionResult(text="hello", language=language)


# ---------------------------------------------------------------------------
# Supported languages
# ---------------------------------------------------------------------------


def test_english_maps_to_en(detector: LanguageDetector) -> None:
    assert detector.detect(_result("en")) == "en"


def test_japanese_maps_to_ja(detector: LanguageDetector) -> None:
    assert detector.detect(_result("ja")) == "ja"


# ---------------------------------------------------------------------------
# Default fallback
# ---------------------------------------------------------------------------


def test_french_defaults_to_en(detector: LanguageDetector) -> None:
    assert detector.detect(_result("fr")) == "en"


def test_empty_string_defaults_to_en(detector: LanguageDetector) -> None:
    assert detector.detect(_result("")) == "en"


def test_unknown_code_defaults_to_en(detector: LanguageDetector) -> None:
    assert detector.detect(_result("zh")) == "en"


def test_spanish_defaults_to_en(detector: LanguageDetector) -> None:
    assert detector.detect(_result("es")) == "en"


# ---------------------------------------------------------------------------
# Case / whitespace normalisation
# ---------------------------------------------------------------------------


def test_uppercase_en_maps_to_en(detector: LanguageDetector) -> None:
    """Language codes from APIs may arrive in uppercase."""
    assert detector.detect(_result("EN")) == "en"


def test_uppercase_ja_maps_to_ja(detector: LanguageDetector) -> None:
    assert detector.detect(_result("JA")) == "ja"


def test_whitespace_padded_en(detector: LanguageDetector) -> None:
    assert detector.detect(_result("  en  ")) == "en"


def test_whitespace_padded_ja(detector: LanguageDetector) -> None:
    assert detector.detect(_result("  ja  ")) == "ja"
