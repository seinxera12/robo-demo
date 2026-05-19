"""
Groq Whisper STT backend.

Wraps raw PCM16 bytes in a WAV header before sending to the Groq API,
which requires a file-like object rather than raw PCM data.
Uses response_format="verbose_json" to receive both text and language fields.
"""

from __future__ import annotations

import io
import time
import wave

import groq

from server.log import stt_log
from server.models import TranscriptionResult
from server.stt.base import BaseSTTBackend


class GroqSTTBackend:
    """STT backend that uses the Groq Whisper API for transcription."""

    def __init__(self, client: groq.AsyncGroq, model: str = "whisper-large-v3-turbo") -> None:
        self.client = client
        self.model = model

    async def transcribe(self, pcm16_bytes: bytes) -> TranscriptionResult:
        """Transcribe raw PCM16 audio bytes using the Groq Whisper API."""
        audio_kb = len(pcm16_bytes) // 1024
        stt_log.info("transcribe_start  model=%s  audio_kb=%d", self.model, audio_kb)
        t0 = time.monotonic()

        # Build an in-memory WAV file from the raw PCM16 bytes
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm16_bytes)
        wav_buffer.seek(0)
        wav_buffer.name = "audio.wav"

        try:
            result = await self.client.audio.transcriptions.create(
                model=self.model,
                file=wav_buffer,
                response_format="verbose_json",
            )
        except Exception as exc:
            stt_log.error("transcribe_failed  model=%s  error=%s", self.model, exc)
            raise

        latency_ms = int((time.monotonic() - t0) * 1000)
        stt_log.info(
            "transcribe_done  latency_ms=%d  lang=%s  text=%r",
            latency_ms, result.language, result.text,
        )

        return TranscriptionResult(text=result.text, language=result.language)


assert isinstance(GroqSTTBackend, type)
