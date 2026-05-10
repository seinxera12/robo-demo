"""
Groq Whisper STT backend.

Wraps raw PCM16 bytes in a WAV header before sending to the Groq API,
which requires a file-like object rather than raw PCM data.
Uses response_format="verbose_json" to receive both text and language fields.
"""

from __future__ import annotations

import io
import wave

import groq

from server.models import TranscriptionResult
from server.stt.base import BaseSTTBackend


class GroqSTTBackend:
    """STT backend that uses the Groq Whisper API for transcription.

    Accepts raw PCM16 audio at 16 kHz mono and wraps it in a WAV container
    before sending to the Groq API. The language field from the Groq
    verbose_json response is the source of truth for language detection.

    Implements the BaseSTTBackend protocol.
    """

    def __init__(self, client: groq.AsyncGroq, model: str = "whisper-large-v3-turbo") -> None:
        """Initialise the backend.

        Args:
            client: An authenticated AsyncGroq client instance.
            model:  Groq Whisper model name. Defaults to whisper-large-v3-turbo.
        """
        self.client = client
        self.model = model

    async def transcribe(self, pcm16_bytes: bytes) -> TranscriptionResult:
        """Transcribe raw PCM16 audio bytes using the Groq Whisper API.

        Wraps the PCM16 data in a WAV container (1 channel, 16-bit, 16 kHz)
        before sending to the API. The Groq SDK requires the file-like object
        to have a ``name`` attribute ending in a recognised audio extension.

        Args:
            pcm16_bytes: Raw PCM16 audio bytes at 16 kHz, mono.

        Returns:
            TranscriptionResult with the transcribed text and detected language.
        """
        # Build an in-memory WAV file from the raw PCM16 bytes.
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)       # mono
            wf.setsampwidth(2)       # 16-bit samples
            wf.setframerate(16000)   # 16 kHz
            wf.writeframes(pcm16_bytes)

        wav_buffer.seek(0)
        wav_buffer.name = "audio.wav"  # Groq SDK requires a name attribute

        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=wav_buffer,
            response_format="verbose_json",
        )

        return TranscriptionResult(text=result.text, language=result.language)


# Verify the class satisfies the protocol at import time (development guard).
assert isinstance(GroqSTTBackend, type)
