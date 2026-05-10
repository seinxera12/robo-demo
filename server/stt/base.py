"""
Base protocol for Speech-to-Text backends.

All STT backends must implement the BaseSTTBackend protocol,
enabling the pipeline to swap implementations without code changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from typing import Protocol, runtime_checkable
except ImportError:
    from typing_extensions import Protocol, runtime_checkable  # type: ignore[assignment]

from server.models import TranscriptionResult


@runtime_checkable
class BaseSTTBackend(Protocol):
    """Protocol that all STT backend implementations must satisfy."""

    async def transcribe(self, audio_bytes: bytes) -> TranscriptionResult:
        """Transcribe audio bytes and return a TranscriptionResult.

        Args:
            audio_bytes: Raw audio bytes to transcribe. Implementations
                         may expect a specific format (e.g. PCM16 or WAV).

        Returns:
            TranscriptionResult with the transcribed text and detected language.
        """
        ...
