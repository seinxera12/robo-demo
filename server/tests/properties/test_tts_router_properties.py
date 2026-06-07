"""
Property-based tests for server/tts/tts_router.py :: _detect_language_from_text()

Feature: building-nav-integration
Property 8 from design.md
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Access the private function directly for unit testing
from server.tts.tts_router import _detect_language_from_text

_HIRAGANA = [chr(c) for c in range(0x3040, 0x30A0)]
_KATAKANA = [chr(c) for c in range(0x30A0, 0x3100)]
_CJK = [chr(c) for c in range(0x4E00, 0x9FFF + 1)]


# ---------------------------------------------------------------------------
# Property 8: TTSRouter text-based language discrimination
# ---------------------------------------------------------------------------

@given(
    kana=st.sampled_from(_HIRAGANA + _KATAKANA),
    suffix=st.text(),
)
@settings(max_examples=300)
def test_kana_text_routes_to_ja(kana: str, suffix: str) -> None:
    """Feature: building-nav-integration, Property 8a: kana → ja.

    For ANY string containing hiragana or katakana, _detect_language_from_text()
    MUST return "ja", even when CJK ideographs are also present.
    Validates: Requirements 7.7, 7.8
    """
    text = kana + suffix
    result = _detect_language_from_text(text)
    assert result == "ja", (
        f"Expected 'ja' for kana-containing text, got {result!r}. "
        f"kana={kana!r}"
    )


@given(cjk_char=st.sampled_from(_CJK))
@settings(max_examples=300)
def test_cjk_only_routes_to_zh(cjk_char: str) -> None:
    """Feature: building-nav-integration, Property 8b: CJK-only → zh.

    For ANY string containing only CJK Unified Ideographs (no kana),
    _detect_language_from_text() MUST return "zh".
    Validates: Requirements 7.7
    """
    # Construct a pure CJK string — no kana, no Latin
    text = cjk_char
    result = _detect_language_from_text(text)
    assert result == "zh", (
        f"Expected 'zh' for CJK-only text, got {result!r}. cjk={cjk_char!r}"
    )


@given(
    text=st.text(
        alphabet=st.characters(
            blacklist_categories=("Lo",),   # exclude most CJK / kana
        ),
        min_size=1,
    )
)
@settings(max_examples=300)
def test_no_cjk_routes_to_en(text: str) -> None:
    """Feature: building-nav-integration, Property 8c: no CJK/kana → en.

    For ANY string that contains neither CJK ideographs nor kana characters,
    _detect_language_from_text() MUST return "en".
    Validates: Requirements 7.8
    """
    import re
    _cjk_kana_re = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
    if _cjk_kana_re.search(text):
        return  # hypothesis supplied a CJK char via blacklist_categories edge-case; skip

    result = _detect_language_from_text(text)
    assert result == "en", (
        f"Expected 'en' for non-CJK text, got {result!r}. text={text!r}"
    )
