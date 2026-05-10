"""
AudioCapture — continuous microphone capture using sounddevice.

Opens a sounddevice.InputStream at 16 kHz, 1 channel, int16 PCM,
with 32ms frames (512 samples). Each frame is forwarded to the
provided VAD callback.

Requirements: 2.1
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Frame parameters
SAMPLE_RATE = 16_000          # 16 kHz
CHANNELS = 1
DTYPE = "int16"
FRAME_SAMPLES = 512           # 32ms at 16 kHz


class AudioCapture:
    """Captures microphone audio and forwards each 32ms PCM16 frame to a callback.

    Args:
        vad_callback: Called with raw bytes for each captured frame.
    """

    def __init__(self, vad_callback: Callable[[bytes], None]) -> None:
        self._vad_callback = vad_callback
        self._stream: object | None = None  # sounddevice.InputStream

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the microphone stream and begin capturing audio."""
        try:
            import sounddevice as sd  # imported lazily so the module can be
                                      # imported on machines without PortAudio

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=FRAME_SAMPLES,
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info(
                "AudioCapture started: %d Hz, %d ch, %s, %d samples/frame",
                SAMPLE_RATE,
                CHANNELS,
                DTYPE,
                FRAME_SAMPLES,
            )
        except Exception as exc:
            # Import sounddevice here to check for PortAudioError; if the
            # import itself fails we still want a graceful warning.
            try:
                import sounddevice as sd
                if isinstance(exc, sd.PortAudioError):
                    logger.warning(
                        "Microphone unavailable (PortAudioError): %s. "
                        "Audio capture disabled — use the browser text input instead.",
                        exc,
                    )
                    return
            except ImportError:
                pass
            logger.warning(
                "Failed to open microphone: %s. "
                "Audio capture disabled — use the browser text input instead.",
                exc,
            )

    def stop(self) -> None:
        """Stop capturing audio and close the microphone stream."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
                logger.info("AudioCapture stopped.")
            except Exception as exc:
                logger.warning("Error stopping AudioCapture: %s", exc)
            finally:
                self._stream = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: object,
        frames: int,
        time: object,
        status: object,
    ) -> None:
        """sounddevice callback — called on the audio thread for each frame."""
        if status:
            logger.debug("AudioCapture status: %s", status)

        # indata is a numpy array of shape (FRAME_SAMPLES, 1) with dtype int16.
        # Convert to raw bytes and forward to the VAD callback.
        frame_bytes: bytes = indata.tobytes()
        self._vad_callback(frame_bytes)
