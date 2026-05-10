"""
AudioPlayback — WAV audio playback via sounddevice.

Receives WAV bytes from the WebSocket client, decodes them, converts to
float32, and plays through the system speaker at 24 kHz using a
sounddevice.OutputStream.

Signals ``on_playback_done`` when the playback queue is empty.

Requirements: 1.8
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import Callable

logger = logging.getLogger(__name__)

# Output audio parameters (must match server TTS output)
SAMPLE_RATE = 24_000   # 24 kHz
CHANNELS = 1


class AudioPlayback:
    """Plays WAV audio chunks received from the server.

    Args:
        on_playback_done: Optional callback invoked when the playback queue
                          becomes empty (i.e. all queued audio has been played).
    """

    def __init__(self, on_playback_done: Callable[[], None] | None = None) -> None:
        self._on_playback_done = on_playback_done
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, wav_bytes: bytes) -> None:
        """Add WAV bytes to the playback queue.

        Args:
            wav_bytes: Complete WAV file bytes (with header) at 24 kHz.
        """
        await self._queue.put(wav_bytes)
        logger.debug("AudioPlayback enqueued %d bytes", len(wav_bytes))

    async def run(self) -> None:
        """Main playback loop — dequeues and plays WAV chunks sequentially."""
        self._running = True
        logger.info("AudioPlayback started.")

        while self._running:
            try:
                # Wait for the next chunk (timeout allows checking _running flag)
                wav_bytes = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            if wav_bytes is None:
                # Sentinel value — stop the loop
                break

            await self._play_wav(wav_bytes)
            self._queue.task_done()

            # If the queue is now empty, signal playback_done
            if self._queue.empty():
                logger.debug("AudioPlayback queue empty — signalling playback_done.")
                if self._on_playback_done is not None:
                    self._on_playback_done()

        logger.info("AudioPlayback stopped.")

    def stop(self) -> None:
        """Stop playback immediately and clear the queue."""
        self._running = False
        # Drain the queue so pending chunks are discarded
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        logger.debug("AudioPlayback stopped and queue cleared.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _play_wav(self, wav_bytes: bytes) -> None:
        """Decode WAV bytes and play through sounddevice.

        Runs the blocking sounddevice call in a thread executor so it does
        not block the asyncio event loop.

        Args:
            wav_bytes: Complete WAV file bytes.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._play_wav_blocking, wav_bytes)

    def _play_wav_blocking(self, wav_bytes: bytes) -> None:
        """Blocking WAV decode and playback (runs in a thread executor)."""
        try:
            import numpy as np
            import sounddevice as sd

            # Decode WAV
            with wave.open(io.BytesIO(wav_bytes)) as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()
                raw_frames = wf.readframes(wf.getnframes())

            if sample_width != 2:
                logger.warning(
                    "AudioPlayback: unexpected sample width %d (expected 2). "
                    "Skipping chunk.",
                    sample_width,
                )
                return

            # Convert int16 PCM → float32 in [-1.0, 1.0]
            audio_int16 = np.frombuffer(raw_frames, dtype=np.int16)
            audio_float32 = audio_int16.astype(np.float32) / 32768.0

            # Reshape for multi-channel if needed
            if n_channels > 1:
                audio_float32 = audio_float32.reshape(-1, n_channels)

            logger.debug(
                "AudioPlayback playing %d samples at %d Hz (%d ch)",
                len(audio_float32),
                framerate,
                n_channels,
            )

            sd.play(audio_float32, samplerate=framerate, blocking=True)

        except Exception as exc:
            logger.warning("AudioPlayback._play_wav_blocking error: %s", exc)
