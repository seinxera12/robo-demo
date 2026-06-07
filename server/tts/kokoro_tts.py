"""
Kokoro TTS backends for English and Japanese synthesis.

Both classes produce 24 kHz WAV bytes (complete WAV file with header).
Synthesis runs in a thread executor to avoid blocking the asyncio event loop.
"""

import asyncio
import io
import time
from typing import Optional

import numpy as np
import soundfile as sf

from server.log import tts_log
from server.config import KOKORO_MODEL_DIR



class KokoroTTS:
    """English TTS using Kokoro KPipeline(lang_code='a')."""

    DEFAULT_VOICE = "af_heart"

    def __init__(self) -> None:
        self._pipeline: Optional[object] = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            tts_log.info("init_pipeline  engine=KokoroTTS  lang=en")
            
            self._pipeline = KPipeline(
                lang_code='a',
            )

            tts_log.info("pipeline_ready  engine=KokoroTTS  lang=en")
        return self._pipeline

    def _synthesize_sync(self, text: str) -> bytes:
        t0 = time.monotonic()
        pipeline = self._get_pipeline()
        audio_chunks = []
        for _, _, audio in pipeline(text, voice=self.DEFAULT_VOICE):
            audio_chunks.append(audio)
        if not audio_chunks:
            tts_log.warning("synthesis_empty  engine=KokoroTTS  text=%r", text[:80])
            return b""
        audio_array = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, audio_array, 24000, format='WAV')
        buf.seek(0)
        wav_bytes = buf.read()
        ms = int((time.monotonic() - t0) * 1000)
        # Estimate audio duration from sample count
        duration_ms = int(len(audio_array) / 24000 * 1000)
        tts_log.info(
            "synthesised  engine=KokoroTTS  text=%r  wav_bytes=%d  "
            "synthesis_ms=%d  audio_duration_ms=%d",
            text[:80], len(wav_bytes), ms, duration_ms,
        )
        return wav_bytes

    def warm_up(self) -> None:
        """Pre-load the Kokoro pipeline and voice file without producing audio.

        Calls _get_pipeline() to load the model, then load_voice() to download
        and cache the voice .pt file. This ensures the first synthesis call has
        no cold-start or network-fetch penalty.

        Requirements: 3.4
        """
        pipeline = self._get_pipeline()
        pipeline.load_voice(self.DEFAULT_VOICE)
        tts_log.info("warm_up_complete  engine=KokoroTTS  voice=%s", self.DEFAULT_VOICE)

    async def synthesize(self, text: str) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text)


class KokoroJapaneseTTS:
    """Japanese TTS using Kokoro KPipeline(lang_code='j')."""

    DEFAULT_VOICE = "jf_alpha"

    def __init__(self) -> None:
        self._pipeline: Optional[object] = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            tts_log.info("init_pipeline  engine=KokoroJapaneseTTS  lang=ja")

            self._pipeline = KPipeline(
                lang_code='j'
            )
            
            tts_log.info("pipeline_ready  engine=KokoroJapaneseTTS  lang=ja")
        return self._pipeline

    def _synthesize_sync(self, text: str) -> bytes:
        t0 = time.monotonic()
        pipeline = self._get_pipeline()
        audio_chunks = []
        for _, _, audio in pipeline(text, voice=self.DEFAULT_VOICE):
            audio_chunks.append(audio)
        if not audio_chunks:
            tts_log.warning("synthesis_empty  engine=KokoroJapaneseTTS  text=%r", text[:80])
            return b""
        audio_array = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, audio_array, 24000, format='WAV')
        buf.seek(0)
        wav_bytes = buf.read()
        ms = int((time.monotonic() - t0) * 1000)
        duration_ms = int(len(audio_array) / 24000 * 1000)
        tts_log.info(
            "synthesised  engine=KokoroJapaneseTTS  text=%r  wav_bytes=%d  "
            "synthesis_ms=%d  audio_duration_ms=%d",
            text[:80], len(wav_bytes), ms, duration_ms,
        )
        return wav_bytes

    def warm_up(self) -> None:
        """Pre-load the Kokoro Japanese pipeline and voice file without producing audio.

        Calls _get_pipeline() to load the model, then load_voice() to download
        and cache the voice .pt file. This ensures the first synthesis call has
        no cold-start or network-fetch penalty.

        Requirements: 3.5
        """
        pipeline = self._get_pipeline()
        pipeline.load_voice(self.DEFAULT_VOICE)
        tts_log.info("warm_up_complete  engine=KokoroJapaneseTTS  voice=%s", self.DEFAULT_VOICE)

    async def synthesize(self, text: str) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text)


class KokoroChineseTTS:
    """Mandarin Chinese TTS using Kokoro KPipeline(lang_code='z')."""

    DEFAULT_VOICE = "zf_xiaobei"

    def __init__(self) -> None:
        self._pipeline: Optional[object] = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            tts_log.info("init_pipeline  engine=KokoroChineseTTS  lang=zh")
            self._pipeline = KPipeline(lang_code='z')
            tts_log.info("pipeline_ready  engine=KokoroChineseTTS  lang=zh")
        return self._pipeline

    def _synthesize_sync(self, text: str) -> bytes:
        t0 = time.monotonic()
        pipeline = self._get_pipeline()
        audio_chunks = []
        for _, _, audio in pipeline(text, voice=self.DEFAULT_VOICE):
            audio_chunks.append(audio)
        if not audio_chunks:
            tts_log.warning("synthesis_empty  engine=KokoroChineseTTS  text=%r", text[:80])
            return b""
        audio_array = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, audio_array, 24000, format='WAV')
        buf.seek(0)
        wav_bytes = buf.read()
        ms = int((time.monotonic() - t0) * 1000)
        duration_ms = int(len(audio_array) / 24000 * 1000)
        tts_log.info(
            "synthesised  engine=KokoroChineseTTS  text=%r  wav_bytes=%d  "
            "synthesis_ms=%d  audio_duration_ms=%d",
            text[:80], len(wav_bytes), ms, duration_ms,
        )
        return wav_bytes

    def warm_up(self) -> None:
        """Pre-load the Kokoro Chinese pipeline and voice file without producing audio."""
        pipeline = self._get_pipeline()
        pipeline.load_voice(self.DEFAULT_VOICE)
        tts_log.info("warm_up_complete  engine=KokoroChineseTTS  voice=%s", self.DEFAULT_VOICE)

    async def synthesize(self, text: str) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text)
