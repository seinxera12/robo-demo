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
  - `,` and `、` act as soft boundaries when the buffer is already ≥
    _COMMA_FLUSH_MIN_CHARS characters (Fix A). This prevents very long sentences
    from delaying synthesis — audio starts at the first natural pause point.
  - If the buffer exceeds _HARD_FLUSH_CHARS without any boundary, it is flushed
    immediately at the next token boundary (Fix B). This is a safety net for
    pathological cases like run-on sentences with no punctuation.
"""

import logging
import re
from typing import Optional

from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS, KokoroChineseTTS

logger = logging.getLogger(__name__)

# Japanese sentence-ending characters — always a hard boundary
_JP_BOUNDARIES = frozenset('。？！')

# English hard boundaries — always end a sentence
_EN_HARD_BOUNDARIES = frozenset('?!')

# Soft comma boundaries — flush when buffer length exceeds this threshold.
# English comma and Japanese reading comma (、).
_COMMA_CHARS = frozenset(',、')
_COMMA_FLUSH_MIN_CHARS = 60   # Fix A: chars in buffer before a comma triggers a flush

# Hard flush cap — flush the entire buffer if it grows beyond this length
# without hitting any boundary (Fix B).
_HARD_FLUSH_CHARS = 120

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

# Language detection patterns for TTS routing.
# Priority when detecting from text: JA (kana) > ZH (CJK ideographs only) > EN
_HIRAGANA_KATAKANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')   # Hiragana + Katakana
_CJK_IDEOGRAPH = re.compile(r'[\u4e00-\u9fff]')                     # CJK Unified Ideographs


def _detect_language_from_text(text: str) -> str:
    """Return 'ja', 'zh', or 'en' based on Unicode character ranges in *text*.

    Priority order:
      1. Hiragana or Katakana present → 'ja' (Japanese — kana is unambiguous)
      2. CJK Unified Ideographs present (no kana) → 'zh' (Mandarin Chinese)
      3. Neither → 'en' (English / other Latin-script language)
    """
    if _HIRAGANA_KATAKANA.search(text):
        return "ja"
    if _CJK_IDEOGRAPH.search(text):
        return "zh"
    return "en"


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

    def __init__(self, en_tts: KokoroTTS, ja_tts: KokoroJapaneseTTS, zh_tts: KokoroChineseTTS) -> None:
        self._en_tts = en_tts
        self._ja_tts = ja_tts
        self._zh_tts = zh_tts
        self._buffer: str = ""

    async def synthesize(self, text: str, language: str) -> bytes:
        """Synthesize *text* using the TTS engine appropriate for *language*.

        Language routing uses two signals in priority order:
          1. Text-based auto-detection via Unicode ranges (kana → JA, CJK-only → ZH).
          2. Explicit *language* hint — overrides auto-detection when the detected
             effective language differs and *language* is a known supported code.
             This handles the text-input path where text may be purely ASCII even
             when the session language is 'ja' or 'zh'.

        Returns 24 kHz WAV bytes (complete WAV file with header).
        """
        from server.log import tts_log
        effective = _detect_language_from_text(text)
        # Allow the explicit language hint to override when text-based detection
        # disagrees and the hint is a known synthesisable code.
        if effective != language and language in ("en", "ja", "zh"):
            effective = language

        if effective == "ja":
            tts_log.info(
                "route  text=%r  passed_lang=%s  effective_lang=ja  engine=KokoroJapaneseTTS",
                text[:40], language,
            )
            return await self._ja_tts.synthesize(text)
        elif effective == "zh":
            tts_log.info(
                "route  text=%r  passed_lang=%s  effective_lang=zh  engine=KokoroChineseTTS",
                text[:40], language,
            )
            return await self._zh_tts.synthesize(text)
        else:
            tts_log.info(
                "route  text=%r  passed_lang=%s  effective_lang=en  engine=KokoroTTS",
                text[:40], language,
            )
            return await self._en_tts.synthesize(text)

    def accumulate(self, token: str) -> Optional[str]:
        """Append *token* to the internal buffer.

        Returns the accumulated sentence when a boundary is detected, then
        resets the buffer to the remainder.  Returns None if no boundary yet.

        Boundary priority (highest to lowest):
          1. Hard sentence boundaries: . ? ! 。？！ — always flush.
          2. Soft comma boundaries (Fix A): , 、 — flush when buffer ≥
             _COMMA_FLUSH_MIN_CHARS. Breaks long sentences at natural pauses
             so synthesis starts sooner.
          3. Hard character cap (Fix B): flush the whole buffer when it exceeds
             _HARD_FLUSH_CHARS, regardless of punctuation. Safety net for
             run-on sentences.
        """
        self._buffer += token

        # Pass 1: scan for hard sentence boundaries and soft comma boundaries
        for i, ch in enumerate(self._buffer):
            # Hard sentence boundary
            if ch in _JP_BOUNDARIES or ch in _EN_HARD_BOUNDARIES or ch == '.':
                if _is_sentence_boundary(self._buffer, i):
                    sentence = self._buffer[: i + 1].strip()
                    self._buffer = self._buffer[i + 1:]
                    if sentence:
                        return sentence

            # Fix A — soft comma boundary: only when buffer is long enough
            if ch in _COMMA_CHARS and len(self._buffer) >= _COMMA_FLUSH_MIN_CHARS:
                sentence = self._buffer[: i + 1].strip()
                self._buffer = self._buffer[i + 1:]
                if sentence:
                    return sentence

        # Fix B — hard character cap: flush entire buffer if it's too long
        if len(self._buffer) >= _HARD_FLUSH_CHARS:
            sentence = self._buffer.strip()
            self._buffer = ""
            if sentence:
                return sentence

        return None

    def flush(self) -> Optional[str]:
        """Return any remaining buffered text and reset the buffer."""
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if remaining else None
