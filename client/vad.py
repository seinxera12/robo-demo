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
import time
from typing import Callable

logger = logging.getLogger(__name__)

# Audio constants
SAMPLE_RATE = 16_000          # Hz
FRAME_SAMPLES = 512           # 32ms at 16 kHz
FRAME_MS = 32                 # milliseconds per frame
SPEECH_THRESHOLD = 0.3        # probability threshold for speech detection.
                              # Silero VAD scores Japanese and other non-English
                              # languages lower than English — 0.3 is the
                              # recommended multilingual threshold per the Silero
                              # VAD paper.  0.5 causes Japanese frames to be
                              # silently dropped, leaving the button stuck open.

# Barge-in protection: minimum seconds after set_speaking(True) before a
# barge-in can fire.  Prevents the TTS speaker output from feeding back into
# the mic and triggering a false interrupt during the first ~500ms of playback.
_BARGE_IN_COOLDOWN_S = 0.5

# Barge-in debounce: minimum seconds between consecutive barge-in callbacks.
# Prevents a single loud frame from firing the callback multiple times.
_BARGE_IN_DEBOUNCE_S = 1.0


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
        self._barge_in_enabled: bool = False     # True only when robo_active (button pressed)

        # Barge-in timing guards
        self._speaking_started_at: float = 0.0  # monotonic time when set_speaking(True) was called
        self._last_barge_in_at: float = 0.0     # monotonic time of last barge-in callback

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
                # Barge-in: user started speaking while TTS is playing.
                # Only fire if barge-in is enabled (robo_active / button was pressed).
                # Without this gate, ambient mic audio during TTS playback would send
                # a spurious interrupt even though the user never pressed the button.
                if not self._barge_in_enabled:
                    logger.debug("Barge-in suppressed (barge_in_enabled=False)")
                    return
                # Guard 1 — cooldown: ignore frames in the first _BARGE_IN_COOLDOWN_S
                # after playback started (speaker bleed / echo protection).
                # Guard 2 — debounce: ignore if we already fired a barge-in recently.
                now = time.monotonic()
                cooldown_elapsed = now - self._speaking_started_at
                debounce_elapsed = now - self._last_barge_in_at
                if (cooldown_elapsed < _BARGE_IN_COOLDOWN_S or
                        debounce_elapsed < _BARGE_IN_DEBOUNCE_S):
                    logger.debug(
                        "Barge-in suppressed (cooldown=%.2fs, debounce=%.2fs)",
                        cooldown_elapsed, debounce_elapsed,
                    )
                    return
                logger.debug("Barge-in detected (prob=%.3f)", speech_prob)
                self._last_barge_in_at = now
                self._on_barge_in()
                # Clear _client_speaking immediately so that frames arriving
                # before the server responds with "listening" are accumulated
                # as normal speech rather than being dropped by the barge-in
                # branch.  The server will also call set_speaking(False) when
                # it transitions to "listening", which is idempotent here.
                self._client_speaking = False
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
        if is_speaking:
            self._speaking_started_at = time.monotonic()
        logger.debug("VAD client_speaking set to %s", is_speaking)

    def set_barge_in_enabled(self, enabled: bool) -> None:
        """Arm or disarm barge-in detection.

        Must be True (robo_active / button pressed) for barge-in to fire.
        Disarmed automatically when the server sends robo_deactivated.

        Args:
            enabled: True to allow barge-in; False to suppress it.
        """
        self._barge_in_enabled = enabled
        logger.debug("VAD barge_in_enabled set to %s", enabled)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_model(self) -> object | None:
        """Load the Silero VAD model from local bundled file."""
        try:
            import torch  # noqa: F401 — checked at runtime
            import os
            from server.config import MODELS_DIR

            model_path = os.path.join(MODELS_DIR, "silero_vad.jit")
            model = torch.jit.load(model_path)
            model.eval()
            logger.info("Silero VAD model loaded successfully from local bundle.")
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
