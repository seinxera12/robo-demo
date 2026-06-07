"""
POST /api/detect-language — Stateless text-based language detection endpoint.

Detects language from Unicode character ranges without calling any external API.
Used by building-nav when text is submitted via keyboard (no STT language hint).

Priority: KO (Hangul) > JA (kana) > ZH (CJK ideographs) > EN (default)
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

router = APIRouter()

# Compiled Unicode-range patterns
_HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff]")          # Hangul syllables + Jamo
_HIRAGANA_KATAKANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")  # Hiragana + Katakana
_CJK_IDEOGRAPH = re.compile(r"[\u4e00-\u9fff]")                 # CJK Unified Ideographs


def detect_language_from_text(text: str) -> str:
    """Return the most likely language code for *text* based on Unicode ranges.

    Detection priority (highest to lowest):
      1. Hangul syllables/Jamo → ``"ko"``
      2. Hiragana or Katakana  → ``"ja"``
      3. CJK Unified Ideographs (without kana) → ``"zh"``
      4. Default → ``"en"``

    This is a pure, stateless function — identical inputs always produce
    identical outputs regardless of call order.
    """
    if _HANGUL.search(text):
        return "ko"
    if _HIRAGANA_KATAKANA.search(text):
        return "ja"
    if _CJK_IDEOGRAPH.search(text):
        return "zh"
    return "en"


class DetectLanguageRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must be a non-empty, non-whitespace string")
        return v


class DetectLanguageResponse(BaseModel):
    language: str


@router.post("/detect-language", response_model=DetectLanguageResponse)
async def detect_language_endpoint(body: DetectLanguageRequest) -> DetectLanguageResponse:
    """Detect language from text using Unicode character ranges.

    - Returns ``{"language": "en"|"ja"|"zh"|"ko"}`` with HTTP 200.
    - Returns HTTP 422 for absent or empty ``text``.
    - Stateless: no external calls, no app.state access needed.
    """
    return DetectLanguageResponse(language=detect_language_from_text(body.text))
