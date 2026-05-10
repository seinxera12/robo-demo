"""
Kokoro TTS backends for English and Japanese synthesis.

Both classes produce 24 kHz WAV bytes (complete WAV file with header).
Synthesis runs in a thread executor to avoid blocking the asyncio event loop.
"""

import asyncio
import io
import logging
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


class KokoroTTS:
    """English TTS using Kokoro KPipeline(lang_code='a')."""

    # Default English voice — can be overridden if needed
    DEFAULT_VOICE = "af_heart"

    def __init__(self) -> None:
        self._pipeline: Optional[object] = None

    def _get_pipeline(self):
        """Lazily instantiate the KPipeline on first use."""
        if self._pipeline is None:
            from kokoro import KPipeline
            logger.info("Initialising KokoroTTS (English) pipeline...")
            self._pipeline = KPipeline(lang_code='a')
            logger.info("KokoroTTS (English) pipeline ready.")
        return self._pipeline

    def _synthesize_sync(self, text: str) -> bytes:
        """Synchronous synthesis — runs in a thread executor."""
        pipeline = self._get_pipeline()
        audio_chunks = []
        for _, _, audio in pipeline(text, voice=self.DEFAULT_VOICE):
            audio_chunks.append(audio)
        if not audio_chunks:
            return b""
        audio_array = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, audio_array, 24000, format='WAV')
        buf.seek(0)
        return buf.read()

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to 24 kHz WAV bytes (async, non-blocking)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text)


class KokoroJapaneseTTS:
    """Japanese TTS using Kokoro KPipeline(lang_code='j')."""

    DEFAULT_VOICE = "jf_alpha"

    def __init__(self) -> None:
        self._pipeline: Optional[object] = None

    def _get_pipeline(self):
        """Lazily instantiate the KPipeline on first use."""
        if self._pipeline is None:
            from kokoro import KPipeline
            logger.info("Initialising KokoroJapaneseTTS pipeline...")
            self._pipeline = KPipeline(lang_code='j')
            logger.info("KokoroJapaneseTTS pipeline ready.")
        return self._pipeline

    def _synthesize_sync(self, text: str) -> bytes:
        """Synchronous synthesis — runs in a thread executor."""
        pipeline = self._get_pipeline()
        audio_chunks = []
        for _, _, audio in pipeline(text, voice=self.DEFAULT_VOICE):
            audio_chunks.append(audio)
        if not audio_chunks:
            return b""
        audio_array = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, audio_array, 24000, format='WAV')
        buf.seek(0)
        return buf.read()

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to 24 kHz WAV bytes (async, non-blocking)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text)
