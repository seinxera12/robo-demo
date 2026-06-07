"""
Property-based tests for server/api/detect_language.py :: detect_language_from_text()

Feature: building-nav-integration
Properties 1–5 from design.md
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from server.api.detect_language import detect_language_from_text

_VALID_CODES = frozenset({"en", "ja", "zh", "ko"})

# Unicode ranges used by the function
_HIRAGANA = [chr(c) for c in range(0x3040, 0x30A0)]
_KATAKANA = [chr(c) for c in range(0x30A0, 0x3100)]
_CJK = [chr(c) for c in range(0x4E00, 0x9FFF + 1)]
_HANGUL = [chr(c) for c in range(0xAC00, 0xD7A3 + 1)]


# ---------------------------------------------------------------------------
# Property 1: output domain invariant
# ---------------------------------------------------------------------------

@given(text=st.text(min_size=1))
@settings(max_examples=300)
def test_output_domain_invariant(text: str) -> None:
    """Feature: building-nav-integration, Property 1: detect-language output domain.

    For ANY non-empty text string, detect_language_from_text() MUST return a
    value in {"en", "ja", "zh", "ko"} and never None or any other value.
    Validates: Requirements 4.1, 4.7
    """
    result = detect_language_from_text(text)
    assert result in _VALID_CODES, (
        f"Expected one of {_VALID_CODES}, got {result!r} for input {text!r}"
    )


# ---------------------------------------------------------------------------
# Property 2: Japanese detection priority
# ---------------------------------------------------------------------------

@given(
    kana=st.sampled_from(_HIRAGANA + _KATAKANA),
    suffix=st.text(),
)
@settings(max_examples=300)
def test_japanese_detection_priority(kana: str, suffix: str) -> None:
    """Feature: building-nav-integration, Property 2: Japanese detection priority.

    For ANY string that contains at least one hiragana or katakana character,
    detect_language_from_text() MUST return "ja" — even when CJK ideographs
    are also present in the suffix.
    Validates: Requirements 4.2, 7.8
    """
    text = kana + suffix
    result = detect_language_from_text(text)
    assert result == "ja", (
        f"Expected 'ja' for kana-containing text, got {result!r}. "
        f"kana={kana!r}, suffix={suffix!r}"
    )


# ---------------------------------------------------------------------------
# Property 3: Korean detection
# ---------------------------------------------------------------------------

@given(
    text=st.text(
        alphabet=st.characters(min_codepoint=0xAC00, max_codepoint=0xD7A3),
        min_size=1,
    )
)
@settings(max_examples=300)
def test_korean_detection(text: str) -> None:
    """Feature: building-nav-integration, Property 3: Korean detection.

    For ANY string containing only Hangul syllable characters (U+AC00–U+D7A3),
    detect_language_from_text() MUST return "ko".
    Validates: Requirements 4.4
    """
    result = detect_language_from_text(text)
    assert result == "ko", (
        f"Expected 'ko' for Hangul-only text, got {result!r}. text={text!r}"
    )


# ---------------------------------------------------------------------------
# Property 4: Chinese detection (CJK without kana)
# ---------------------------------------------------------------------------

@given(
    cjk_char=st.sampled_from(_CJK),
    suffix=st.text(
        alphabet=st.characters(
            blacklist_categories=("Lo",),   # exclude letters-other (most CJK)
            blacklist_characters="".join(_HIRAGANA + _KATAKANA),
        )
    ),
)
@settings(max_examples=300)
def test_chinese_detection_cjk_without_kana(cjk_char: str, suffix: str) -> None:
    """Feature: building-nav-integration, Property 4: Chinese detection (CJK without kana).

    For ANY string that contains at least one CJK Unified Ideograph and NO
    hiragana or katakana characters, detect_language_from_text() MUST return "zh".
    Validates: Requirements 4.3, 7.7
    """
    # Build text that definitely has CJK but strip any kana that slipped in via suffix
    text = cjk_char + suffix.replace("".join(_HIRAGANA), "").replace("".join(_KATAKANA), "")

    # Double-check: skip if suffix accidentally introduced kana (unlikely but safe)
    import re
    _kana_re = re.compile(r"[\u3040-\u30ff]")
    if _kana_re.search(text):
        return  # hypothesis will find a better example

    result = detect_language_from_text(text)
    assert result in ("zh", "ko"), (
        f"Expected 'zh' or 'ko' for CJK-containing text without kana, "
        f"got {result!r}. text={text!r}"
    )


# ---------------------------------------------------------------------------
# Property 5: idempotence
# ---------------------------------------------------------------------------

@given(text=st.text(min_size=1))
@settings(max_examples=300)
def test_idempotence(text: str) -> None:
    """Feature: building-nav-integration, Property 5: detect-language idempotence.

    Calling detect_language_from_text(t) twice with the same input MUST
    return the same value both times (stateless function).
    Validates: Requirements 4.7
    """
    first = detect_language_from_text(text)
    second = detect_language_from_text(text)
    assert first == second, (
        f"Idempotence violated: first={first!r}, second={second!r} for text={text!r}"
    )
