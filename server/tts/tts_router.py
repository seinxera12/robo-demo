"""
TTSRouter — routes synthesis to the correct Kokoro TTS engine based on language,
and accumulates LLM tokens until a sentence boundary is detected.
"""

import logging
from typing import Optional

from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS

logger = logging.getLogger(__name__)

# Characters that mark the end of a sentence
SENTENCE_BOUNDARY_CHARS = frozenset('.?!。？！')


class TTSRouter:
    """
    Routes TTS synthesis to the appropriate Kokoro pipeline based on language,
    and provides token accumulation with sentence-boundary detection.

    Language routing:
      "en"  → KokoroTTS (English)
      "ja"  → KokoroJapaneseTTS (Japanese)
      other → KokoroTTS (default)
    """

    def __init__(self, en_tts: KokoroTTS, ja_tts: KokoroJapaneseTTS) -> None:
        self._en_tts = en_tts
        self._ja_tts = ja_tts
        self._buffer: str = ""

    async def synthesize(self, text: str, language: str) -> bytes:
        """
        Synthesize *text* using the TTS engine appropriate for *language*.

        Returns 24 kHz WAV bytes (complete WAV file with header).
        """
        if language == "ja":
            logger.debug("TTSRouter: routing to KokoroJapaneseTTS for language=%r", language)
            return await self._ja_tts.synthesize(text)
        else:
            if language != "en":
                logger.debug(
                    "TTSRouter: unknown language %r, defaulting to KokoroTTS (English)", language
                )
            return await self._en_tts.synthesize(text)

    def accumulate(self, token: str) -> Optional[str]:
        """
        Append *token* to the internal buffer.

        Returns the accumulated sentence (including the boundary character) when
        a sentence boundary (`.`, `?`, `!`, `。`, `？`, `！`) is detected,
        then resets the buffer.  Returns None if no boundary has been reached yet.
        """
        self._buffer += token
        # Check whether the buffer now ends with (or contains) a boundary char.
        # We flush at the *first* boundary found so that each sentence is
        # synthesised as soon as it is complete.
        for i, ch in enumerate(self._buffer):
            if ch in SENTENCE_BOUNDARY_CHARS:
                sentence = self._buffer[: i + 1].strip()
                self._buffer = self._buffer[i + 1 :]
                if sentence:
                    return sentence
        return None

    def flush(self) -> Optional[str]:
        """
        Return any remaining buffered text (without a trailing boundary) and
        reset the buffer.  Useful at end-of-stream to avoid dropping the last
        partial sentence.
        """
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if remaining else None
