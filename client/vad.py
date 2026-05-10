"""
SileroVAD — Voice Activity Detection wrapper around the Silero VAD model.

Processes 32ms PCM16 frames (512 samples at 16 kHz) and emits:
  - speech_start  when speech probability exceeds the threshold
  - speech_end    with the accumulated PCM16 buffer after vad_silence_ms of silence
  - barge-in      when speech is detected while the client is in speaking state

Requirements: 2.2, 2.3, 2.4, 2.5
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Audio constants
SAMPLE_RATE = 16_000          # Hz
FRAME_SAMPLES = 512           # 32ms at 16 kHz
FRAME_MS = 32                 # milliseconds per frame
SPEECH_THRESHOLD = 0.5        # probability threshold for speech detection


class SileroVAD:
    """Wraps the Silero VAD model to detect speech start/end events.

    Args:
        on_speech_end:   Called with the accumulated PCM16 bytes when speech ends.
        on_barge_in:     Called (no arguments) when speech is detected while the
                         client is in speaking state.
        vad_silence_ms:  Milliseconds of silence required before emitting speech_end.
                         Default: 600 ms.
    """

    def __init__(
        self,
        on_speech_end: Callable[[bytes], None],
        on_barge_in: Callable[[], None],
        vad_silence_ms: int = 600,
    ) -> None:
        self._on_speech_end = on_speech_end
        self._on_barge_in = on_barge_in
        self._vad_silence_ms = vad_silence_ms

        # Derived threshold: number of consecutive silent frames before speech_end
        self._silence_threshold_frames: int = max(1, vad_silence_ms // FRAME_MS)

        # Internal state
        self._is_speaking: bool = False          # VAD speech-detected state
        self._speech_buffer: bytes = b""         # accumulated PCM16 during speech
        self._silence_frames: int = 0            # consecutive silent frame count
        self._client_speaking: bool = False      # AudioClient TTS playback state

        # Load the Silero VAD model
        self._model = self._load_model()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_frame(self, frame: bytes) -> None:
        """Process a single 32ms PCM16 frame.

        Args:
            frame: Raw PCM16 bytes (512 samples × 2 bytes = 1024 bytes).
        """
        if self._model is None:
            return

        speech_prob = self._infer(frame)

        if speech_prob >= SPEECH_THRESHOLD:
            # --- Speech detected ---
            if self._client_speaking:
                # Barge-in: user started speaking while TTS is playing
                logger.debug("Barge-in detected (prob=%.3f)", speech_prob)
                self._on_barge_in()
                # Reset VAD state so we start fresh after the interrupt
                self._reset_state()
                return

            if not self._is_speaking:
                # Transition: silence → speech
                logger.debug("Speech start detected (prob=%.3f)", speech_prob)
                self._is_speaking = True
                self._silence_frames = 0

            # Accumulate the frame
            self._speech_buffer += frame

        else:
            # --- Silence detected ---
            if self._is_speaking:
                # Still accumulate during the silence window so we don't clip
                self._speech_buffer += frame
                self._silence_frames += 1

                if self._silence_frames >= self._silence_threshold_frames:
                    # Enough silence — emit speech_end
                    logger.debug(
                        "Speech end detected after %d silent frames (%.0f ms)",
                        self._silence_frames,
                        self._silence_frames * FRAME_MS,
                    )
                    buffer = self._speech_buffer
                    self._reset_state()
                    self._on_speech_end(buffer)

    def set_speaking(self, is_speaking: bool) -> None:
        """Update the client speaking state for barge-in detection.

        Args:
            is_speaking: True when the AudioClient is playing back TTS audio.
        """
        self._client_speaking = is_speaking
        logger.debug("VAD client_speaking set to %s", is_speaking)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_model(self) -> object | None:
        """Load the Silero VAD model via torch.hub."""
        try:
            import torch  # noqa: F401 — checked at runtime

            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
            )
            model.eval()
            logger.info("Silero VAD model loaded successfully.")
            return model
        except Exception as exc:
            logger.warning(
                "Failed to load Silero VAD model: %s. VAD disabled.", exc
            )
            return None

    def _infer(self, frame: bytes) -> float:
        """Run the Silero VAD model on a single PCM16 frame.

        Args:
            frame: Raw PCM16 bytes.

        Returns:
            Speech probability in [0.0, 1.0].
        """
        try:
            import torch

            # Convert PCM16 bytes → float32 tensor normalised to [-1, 1]
            audio_tensor = (
                torch.frombuffer(frame, dtype=torch.int16).float() / 32768.0
            )
            speech_prob: float = self._model(audio_tensor, SAMPLE_RATE).item()
            return speech_prob
        except Exception as exc:
            logger.debug("VAD inference error: %s", exc)
            return 0.0

    def _reset_state(self) -> None:
        """Reset internal VAD state after speech_end or barge-in."""
        self._is_speaking = False
        self._speech_buffer = b""
        self._silence_frames = 0
