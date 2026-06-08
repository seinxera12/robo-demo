"""
Groq Whisper STT backend.

Accepts both raw PCM16 bytes (legacy WebSocket path) and pre-encoded container
formats (webm, ogg, wav, mp3, flac) from the REST /api/stt endpoint.
Format is detected automatically via magic bytes — the content-type header
from multipart uploads is not trusted because building-nav sends webm bytes
labelled as audio/wav.
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

# Audio container format detection via magic bytes.
# Order matters — longer/more-specific patterns before shorter ones.
_AUDIO_MAGIC: list[tuple[bytes, str]] = [
    (b'\x1a\x45\xdf\xa3', 'webm'),  # WebM / MKV (EBML header)
    (b'OggS',             'ogg'),   # Ogg container (Opus / Vorbis)
    (b'RIFF',             'wav'),   # WAV (RIFF header)
    (b'fLaC',             'flac'),  # FLAC
    (b'\xff\xfb',         'mp3'),   # MP3 MPEG-1 Layer 3
    (b'\xff\xf3',         'mp3'),   # MP3 MPEG-2 Layer 3
    (b'\xff\xf2',         'mp3'),   # MP3 MPEG-2.5 Layer 3
    (b'ID3',              'mp3'),   # MP3 with ID3 tag header
]


def _detect_audio_format(audio_bytes: bytes) -> str:
    """Return the audio container format detected from the leading magic bytes.

    Returns one of: 'webm', 'ogg', 'wav', 'flac', 'mp3', or 'pcm16'.
    'pcm16' is the fallback for raw PCM16 audio from the WebSocket pipeline —
    it has no container header, so no magic bytes match.
    """
    for magic, fmt in _AUDIO_MAGIC:
        if audio_bytes[:len(magic)] == magic:
            return fmt
    return 'pcm16'


class GroqSTTBackend:
    """STT backend that uses the Groq Whisper API for transcription."""

    def __init__(self, client: groq.AsyncGroq, model: str = "whisper-large-v3-turbo") -> None:
        self.client = client
        self.model = model

    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
        """Transcribe audio bytes using the Groq Whisper API.

        Accepts both raw PCM16 (legacy WebSocket path) and pre-encoded container
        formats (webm, ogg, wav, mp3, flac) from the REST /api/stt endpoint.
        Format is detected automatically via magic bytes — the content-type header
        from multipart uploads is not trusted because building-nav sends webm bytes
        labelled as audio/wav.
        """
        fmt = _detect_audio_format(audio_bytes)
        audio_kb = len(audio_bytes) // 1024
        stt_log.info(
            "transcribe_start  model=%s  audio_kb=%d  format=%s",
            self.model, audio_kb, fmt,
        )
        t0 = time.monotonic()

        if fmt == 'pcm16':
            # Legacy path: raw PCM16 from the WebSocket AudioClient.
            # Must be wrapped in a WAV container before sending to Groq.
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_bytes)
            wav_buffer.seek(0)
            wav_buffer.name = "audio.wav"
            file_obj = wav_buffer
        else:
            # Pre-encoded audio from the REST endpoint — pass bytes directly.
            # Groq accepts webm, ogg, wav, mp3, flac natively.
            file_obj = io.BytesIO(audio_bytes)
            file_obj.name = f"audio.{fmt}"

        try:
            result = await self.client.audio.transcriptions.create(
                model=self.model,
                file=file_obj,
                response_format="verbose_json",
            )
        except Exception as exc:
            stt_log.error("transcribe_failed  model=%s  format=%s  error=%s", self.model, fmt, exc)
            raise

        latency_ms = int((time.monotonic() - t0) * 1000)
        stt_log.info(
            "transcribe_done  latency_ms=%d  lang=%s  format=%s",
            latency_ms, result.language, fmt,
        )
        return TranscriptionResult(text=result.text, language=result.language)
