"""
Property-based tests for server/lang/detector.py :: LanguageDetector

Feature: building-nav-integration
Properties 6–7 from design.md
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from server.lang.detector import LanguageDetector
from server.models import TranscriptionResult

_VALID_CODES = frozenset({"en", "ja", "ko", "zh"})
_SUPPORTED_CODES = ["en", "ja", "ko", "zh"]

_detector = LanguageDetector()


# ---------------------------------------------------------------------------
# Property 6: LanguageDetector output domain invariant
# ---------------------------------------------------------------------------

@given(language=st.text())
@settings(max_examples=300)
def test_language_detector_output_domain(language: str) -> None:
    """Feature: building-nav-integration, Property 6: LanguageDetector output domain invariant.

    For ANY possible TranscriptionResult.language string (empty, unknown, arbitrary),
    LanguageDetector.detect() MUST return a value in {"en", "ja", "ko", "zh"}.
    Validates: Requirements 7.1–7.5
    """
    result_obj = TranscriptionResult(text="test", language=language)
    detected = _detector.detect(result_obj)
    assert detected in _VALID_CODES, (
        f"Expected one of {_VALID_CODES}, got {detected!r} for language={language!r}"
    )


# ---------------------------------------------------------------------------
# Property 7: round-trip for supported codes
# ---------------------------------------------------------------------------

@given(language=st.sampled_from(_SUPPORTED_CODES))
@settings(max_examples=200)
def test_language_detector_roundtrip(language: str) -> None:
    """Feature: building-nav-integration, Property 7: LanguageDetector round-trip for supported codes.

    For ANY TranscriptionResult whose language field is already in the supported
    set {"en", "ja", "ko", "zh"}, LanguageDetector.detect() MUST return the
    exact same code unchanged.
    Validates: Requirements 7.1–7.4
    """
    result_obj = TranscriptionResult(text="test", language=language)
    detected = _detector.detect(result_obj)
    assert detected == language, (
        f"Round-trip failed: input={language!r}, detected={detected!r}"
    )
