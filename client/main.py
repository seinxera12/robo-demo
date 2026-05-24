"""
Client entry point — wires all AudioClient components together.

Loads configuration from environment variables, instantiates all components,
connects callbacks, and runs the asyncio event loop.

Components:
  - AudioCapture  — microphone capture at 16 kHz PCM16
  - SileroVAD     — voice activity detection
  - WSClient      — WebSocket connection to the server
  - AudioPlayback — WAV playback via sounddevice

Callback wiring:
  - VAD speech_end  → WSClient.send_audio
  - VAD barge_in    → WSClient.send_interrupt + AudioPlayback.stop()
  - WSClient audio  → AudioPlayback.enqueue
  - WSClient status → SileroVAD.set_speaking (True when state == "speaking")

Requirements: 2.1, 9.6
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from client.audio_capture import AudioCapture
from client.audio_playback import AudioPlayback
from client.vad import SileroVAD
from client.ws_client import WSClient


def _configure_logging(log_level: str) -> None:
    from server.config import LOG_DIR
    import os
    from logging.handlers import RotatingFileHandler

    level = getattr(logging, log_level.upper(), logging.INFO)
    
    # File handler — writes to same log dir as server
    log_path = os.path.join(LOG_DIR, "audio_client.log")
    fh = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)


async def main() -> None:
    """Async entry point — sets up and runs all client components."""
    # ------------------------------------------------------------------
    # 1. Load configuration from environment
    # ------------------------------------------------------------------
    # Import CONFIG_PATH here (not at module top) to avoid a circular import
    # when the client is imported as part of the launcher.
    from server.config import CONFIG_PATH
    load_dotenv(dotenv_path=CONFIG_PATH, override=False)

    ws_port: int = int(os.getenv("WS_PORT", "8000"))
    server_port: int = int(os.getenv("SERVER_PORT", "8000"))
    vad_silence_ms: int = int(os.getenv("VAD_SILENCE_MS", "600"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    _configure_logging(log_level)
    logger = logging.getLogger(__name__)

    # The AudioClient connects to the dedicated WS port for audio
    server_url = f"ws://localhost:{ws_port}/ws"
    logger.info("AudioClient connecting to %s", server_url)

    # ------------------------------------------------------------------
    # 2. Instantiate components (forward-declare to allow cross-references)
    # ------------------------------------------------------------------

    # Capture the running event loop once here so that callbacks fired from
    # sounddevice's background threads (which have no event loop of their own)
    # can safely schedule coroutines onto it via call_soon_threadsafe.
    loop = asyncio.get_event_loop()

    # AudioPlayback — plays WAV chunks received from the server
    audio_playback = AudioPlayback(
        on_playback_done=lambda: logger.debug("Playback done — queue empty.")
    )

    # WSClient — WebSocket connection; callbacks wired below
    def _on_audio(wav_bytes: bytes) -> None:
        """Dispatch received WAV bytes to the playback queue."""
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(audio_playback.enqueue(wav_bytes))
        )

    def _on_status(state: str) -> None:
        """Update VAD speaking state based on server status messages."""
        vad.set_speaking(state == "speaking")
        if state == "listening":
            vad.set_speaking(False)   # explicit re-arm (idempotent)
        logger.debug("Status update: %s", state)

    def _on_robo_state(active: bool) -> None:
        """Arm or disarm VAD barge-in when the tap-to-speak button is toggled."""
        vad.set_barge_in_enabled(active)
        logger.debug("Robo active: %s — barge-in %s", active, "armed" if active else "disarmed")

    ws_client = WSClient(
        server_url=server_url,
        on_audio=_on_audio,
        on_status=_on_status,
        on_interrupt=audio_playback.interrupt,
        on_robo_state=_on_robo_state,
    )

    # SileroVAD — voice activity detection; callbacks wired below
    def _on_speech_end(pcm16_bytes: bytes) -> None:
        """Send accumulated PCM16 audio to the server when speech ends."""
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(ws_client.send_audio(pcm16_bytes))
        )

    def _on_barge_in() -> None:
        """Stop local audio immediately, then send interrupt to server."""
        audio_playback.interrupt()  # drain queue + stop stream, loop stays alive
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(ws_client.send_interrupt())
        )

    vad = SileroVAD(
        on_speech_end=_on_speech_end,
        on_barge_in=_on_barge_in,
        vad_silence_ms=vad_silence_ms,
    )

    # AudioCapture — microphone input; forwards frames to VAD
    audio_capture = AudioCapture(vad_callback=vad.process_frame)

    # ------------------------------------------------------------------
    # 3. Start audio capture (graceful degradation if mic unavailable)
    # ------------------------------------------------------------------
    try:
        audio_capture.start()
    except Exception as exc:
        # AudioCapture.start() already logs a warning internally; we catch
        # any unexpected exception here to ensure the client keeps running
        # so the BrowserUI text input can still be used (Requirement 9.6).
        logger.warning(
            "Could not start audio capture: %s. "
            "Continuing without microphone — use the browser text input.",
            exc,
        )

    # ------------------------------------------------------------------
    # 4. Run WebSocket client and audio playback concurrently
    # ------------------------------------------------------------------
    try:
        await asyncio.gather(ws_client.run(), audio_playback.run())
    except asyncio.CancelledError:
        logger.info("AudioClient tasks cancelled — shutting down.")
    finally:
        await ws_client.close()
        audio_playback.stop()
        audio_capture.stop()
        logger.info("AudioClient shut down.")


if __name__ == "__main__":
    asyncio.run(main())
