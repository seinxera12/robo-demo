"""
TTSRouter — routes synthesis to the correct Kokoro TTS engine based on language,
and accumulates LLM tokens until a sentence boundary is detected.

Language routing uses two signals in priority order:
  1. Explicit language code passed to synthesize() — used when STT detected the language.
  2. Auto-detection from the text itself — CJK character presence → Japanese TTS.
     This handles the text-input path where language is always injected as "en".

Sentence boundary detection:
  - `?` and `!` always end a sentence.
  - `.` ends a sentence only when it is NOT an abbreviation, i.e. it is NOT
    preceded by a known short-word pattern (Mr, Dr, Mt, St, vs, etc.) or a
    single capital letter (initials like "U.S.").
  - Japanese boundary characters (。？！) always end a sentence.
"""

import logging
import re
from typing import Optional

from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS

logger = logging.getLogger(__name__)

# Japanese sentence-ending characters — always a hard boundary
_JP_BOUNDARIES = frozenset('。？！')

# English hard boundaries — always end a sentence
_EN_HARD_BOUNDARIES = frozenset('?!')

# Common English abbreviations that end with a period but are NOT sentence ends.
# Matched case-insensitively against the word immediately before the dot.
_ABBREV_RE = re.compile(
    r'\b(?:'
    r'mr|mrs|ms|dr|prof|sr|jr|rev|gen|sgt|cpl|pvt|capt|lt|col|maj'
    r'|st|mt|ft|ave|blvd|dept|est|approx|appt|apt|corp|inc|ltd|co'
    r'|vs|etc|e\.g|i\.e|fig|vol|no|pp|ed|eds|repr|trans'
    r'|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec'
    r'|[a-z]'          # single lowercase letter (list items: a. b. c.)
    r')$',
    re.IGNORECASE,
)

# CJK Unified Ideographs + Hiragana + Katakana + CJK punctuation
_CJK_RE = re.compile(
    r'[\u3000-\u303f'   # CJK punctuation
    r'\u3040-\u309f'    # Hiragana
    r'\u30a0-\u30ff'    # Katakana
    r'\u4e00-\u9fff'    # CJK Unified Ideographs (common)
    r'\uff00-\uffef]'   # Halfwidth/Fullwidth forms
)


def _detect_language_from_text(text: str) -> str:
    """Return 'ja' if the text contains CJK/kana characters, else 'en'."""
    return "ja" if _CJK_RE.search(text) else "en"


def _is_sentence_boundary(buf: str, pos: int) -> bool:
    """Return True if the character at buf[pos] ends a sentence.

    Rules:
    - Japanese boundary chars (。？！) → always True
    - ? and ! → always True
    - . → True only if:
        - followed by whitespace + uppercase (or end of string), AND
        - not preceded by a known abbreviation word
    """
    ch = buf[pos]

    if ch in _JP_BOUNDARIES or ch in _EN_HARD_BOUNDARIES:
        return True

    if ch != '.':
        return False

    # Check what follows the dot
    rest = buf[pos + 1:]
    if rest and not rest[0].isspace():
        # Dot immediately followed by non-space (e.g. "U.S.A", "3.14") — not a boundary
        return False

    # Check what precedes the dot — extract the word before it
    before = buf[:pos]
    word_match = re.search(r'(\w+)$', before)
    if not word_match:
        return True  # nothing before the dot — treat as boundary

    word = word_match.group(1)

    # Single uppercase letter = initial (e.g. "J. Smith") — not a boundary
    if len(word) == 1 and word.isupper():
        return False

    # Known abbreviation — not a boundary
    if _ABBREV_RE.match(word):
        return False

    return True


class TTSRouter:
    """Routes TTS synthesis to the appropriate Kokoro pipeline."""

    def __init__(self, en_tts: KokoroTTS, ja_tts: KokoroJapaneseTTS) -> None:
        self._en_tts = en_tts
        self._ja_tts = ja_tts
        self._buffer: str = ""

    async def synthesize(self, text: str, language: str) -> bytes:
        """Synthesize *text* using the TTS engine appropriate for *language*.

        If *language* is 'ja', or if the text itself contains Japanese/CJK
        characters (auto-detection fallback for the text-input path), routes
        to KokoroJapaneseTTS. Otherwise uses KokoroTTS (English).

        Returns 24 kHz WAV bytes (complete WAV file with header).
        """
        effective_language = _detect_language_from_text(text)
        if effective_language != language:
            logger.debug(
                "TTSRouter: language override %r → %r based on text content",
                language, effective_language,
            )

        if effective_language == "ja":
            from server.log import tts_log
            tts_log.info("route  text=%r  passed_lang=%s  effective_lang=ja  engine=KokoroJapaneseTTS",
                         text[:40], language)
            return await self._ja_tts.synthesize(text)
        else:
            from server.log import tts_log
            tts_log.info("route  text=%r  passed_lang=%s  effective_lang=en  engine=KokoroTTS",
                         text[:40], language)
            return await self._en_tts.synthesize(text)

    def accumulate(self, token: str) -> Optional[str]:
        """Append *token* to the internal buffer.

        Returns the accumulated sentence when a real sentence boundary is
        detected (not an abbreviation period), then resets the buffer.
        Returns None if no boundary reached yet.
        """
        self._buffer += token
        for i, ch in enumerate(self._buffer):
            if ch in _JP_BOUNDARIES or ch in _EN_HARD_BOUNDARIES or ch == '.':
                if _is_sentence_boundary(self._buffer, i):
                    sentence = self._buffer[: i + 1].strip()
                    self._buffer = self._buffer[i + 1:]
                    if sentence:
                        return sentence
        return None

    def flush(self) -> Optional[str]:
        """Return any remaining buffered text and reset the buffer."""
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if remaining else None
