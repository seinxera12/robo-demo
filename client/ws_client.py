"""
WSClient — WebSocket client for the AudioClient process.

Maintains a persistent WebSocket connection to the server, sends binary PCM16
audio frames and JSON control messages, and dispatches received frames to
the appropriate callbacks.

Reconnects with exponential backoff (delays: 1s, 2s, 4s) on disconnect,
up to a maximum of 3 attempts.

Requirements: 1.1, 1.7, 2.5, 9.3, 12.1
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Reconnect backoff delays in seconds (3 attempts max)
_BACKOFF_DELAYS = [1, 2, 4]


class WSClient:
    """WebSocket client that connects to the voice pipeline server.

    Args:
        server_url:  WebSocket URL, e.g. ``ws://localhost:8000/ws``.
        on_audio:    Callback invoked with WAV bytes when a binary frame is received.
        on_status:   Callback invoked with a state string when a status JSON message
                     is received (e.g. ``"listening"``, ``"thinking"``, ``"speaking"``).
        on_interrupt: Optional callback invoked when the server transitions from
                      ``"speaking"`` to ``"listening"`` mid-turn (barge-in interrupt).
                      Used to stop local audio playback immediately.
    """

    def __init__(
        self,
        server_url: str,
        on_audio: Callable[[bytes], None],
        on_status: Callable[[str], None],
        on_interrupt: Callable[[], None] | None = None,
    ) -> None:
        self._server_url = server_url
        self._on_audio = on_audio
        self._on_status = on_status
        self._on_interrupt = on_interrupt
        self._ws = None  # active websockets connection
        self._running: bool = True  # set to False to stop the run() loop
        self._last_state: str = ""  # track previous state to detect speaking→listening

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the WebSocket connection with exponential backoff.

        Tries up to 3 times with delays of 1s, 2s, and 4s between attempts.
        Raises the last exception if all attempts fail.
        """
        import websockets

        last_exc: Exception | None = None
        for attempt, delay in enumerate(_BACKOFF_DELAYS, start=1):
            try:
                self._ws = await websockets.connect(self._server_url)
                logger.info(
                    "WSClient connected to %s (attempt %d)", self._server_url, attempt
                )
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "WSClient connection attempt %d/%d failed: %s. "
                    "Retrying in %ds…",
                    attempt,
                    len(_BACKOFF_DELAYS),
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise ConnectionError(
            f"WSClient failed to connect to {self._server_url} after "
            f"{len(_BACKOFF_DELAYS)} attempts."
        ) from last_exc

    async def send_audio(self, pcm16_bytes: bytes) -> None:
        """Send a binary PCM16 audio frame to the server.

        Args:
            pcm16_bytes: Raw PCM16 audio bytes captured from the microphone.
        """
        if self._ws is None:
            if self._running:
                logger.warning("WSClient.send_audio called but not connected; dropping frame.")
            return
        try:
            await self._ws.send(pcm16_bytes)
        except Exception as exc:
            logger.warning("WSClient.send_audio error: %s", exc)

    async def send_interrupt(self) -> None:
        """Send a JSON interrupt control message to the server."""
        if self._ws is None:
            if self._running:
                logger.warning("WSClient.send_interrupt called but not connected; dropping.")
            return
        try:
            await self._ws.send(json.dumps({"type": "interrupt"}))
            logger.debug("WSClient sent interrupt message.")
        except Exception as exc:
            logger.warning("WSClient.send_interrupt error: %s", exc)

    async def run(self) -> None:
        """Main receive loop — connects and dispatches incoming frames.

        Reconnects with exponential backoff on disconnect. Stops after
        exhausting all reconnect attempts or when ``close()`` is called.
        """
        while self._running:
            try:
                await self.connect()
                await self._receive_loop()
            except ConnectionError as exc:
                # All reconnect attempts exhausted inside connect()
                logger.error("WSClient giving up: %s", exc)
                return
            except asyncio.CancelledError:
                logger.info("WSClient.run() cancelled.")
                return
            except Exception as exc:
                logger.warning("WSClient disconnected unexpectedly: %s", exc)
                # connect() will handle the backoff on the next iteration

    async def close(self) -> None:
        """Stop the run loop and close the active WebSocket connection."""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            finally:
                self._ws = None
        logger.info("WSClient closed.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        """Receive messages from the server and dispatch to callbacks."""
        import websockets

        async for message in self._ws:
            if isinstance(message, bytes):
                # Binary frame → WAV audio for playback
                logger.debug("WSClient received binary frame (%d bytes)", len(message))
                self._on_audio(message)
            elif isinstance(message, str):
                # Text frame → JSON control/status message
                self._dispatch_json(message)

    def _dispatch_json(self, raw: str) -> None:
        """Parse a JSON text frame and invoke the appropriate callback."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("WSClient received invalid JSON: %s — %s", raw[:200], exc)
            return

        msg_type = msg.get("type")
        if msg_type == "status":
            state = msg.get("state", "")
            logger.debug("WSClient received status: %s", state)
            # Detect a server-side barge-in interrupt: speaking → listening.
            # This happens when the server interrupts TTS due to new text input.
            # Fire on_interrupt so the client can stop local audio playback immediately.
            if state == "listening" and self._last_state == "speaking":
                logger.debug("WSClient detected speaking→listening interrupt — stopping playback.")
                if self._on_interrupt is not None:
                    self._on_interrupt()
            self._last_state = state
            self._on_status(state)
        else:
            # Log other message types at debug level; they are not consumed here
            logger.debug("WSClient received JSON message type=%s", msg_type)

