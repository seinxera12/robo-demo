"""
Voice pipeline for the Lightweight Voice Demo server.

Contains PipelineState (the shared state for one WebSocket session) and
VoicePipeline (the five asyncio worker coroutines).

Workers:
  audio_input_worker  — receives PCM16 from AudioClient WebSocket
  stt_worker          — transcribes audio via Groq Whisper API
  llm_worker          — streams LLM tokens, optional Tavily search
  tts_worker          — accumulates tokens to sentence boundary, synthesises WAV
  audio_output_worker — sends WAV chunks to AudioClient, manages state transitions
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from server.lang.detector import LanguageDetector
from server.llm.chain import LLMChain
from server.llm.intent import IntentClassifier
from server.llm.prompt_builder import PromptBuilder
from server.models import TranscriptionResult
from server.search.tavily_search import TavilySearchClient
from server.stt.groq_stt import GroqSTTBackend
from server.tts.tts_router import TTSRouter

logger = logging.getLogger(__name__)

# Sentinel token pushed to token_queue to signal end-of-stream
_END_OF_TOKENS = object()

# Maximum conversation history entries (10 turns × 2 messages per turn)
_MAX_HISTORY_ENTRIES = 20


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------


@dataclass
class PipelineState:
    """Shared mutable state for a single AudioClient WebSocket session.

    One instance is created per connection and passed to all five pipeline
    workers so they can coordinate state transitions and interrupt handling.
    """

    session_id: str
    history: list[dict]              # ConversationHistory, max 10 turns
    state: str                       # "listening" | "thinking" | "speaking"
    interrupt: bool                  # barge-in flag
    detected_language: str           # "en" | "ja", default "en"
    audio_queue: asyncio.Queue       # PCM16 bytes from AudioClient
    transcript_queue: asyncio.Queue  # TranscriptionResult
    token_queue: asyncio.Queue       # str tokens from LLM (or _END_OF_TOKENS sentinel)
    audio_out_queue: asyncio.Queue   # WAV bytes for AudioClient


# ---------------------------------------------------------------------------
# VoicePipeline
# ---------------------------------------------------------------------------


class VoicePipeline:
    """Orchestrates the five asyncio pipeline workers for a single session.

    Args:
        audio_client_ws: The WebSocket connection to the AudioClient.
        state:           The shared PipelineState for this session.
        stt_backend:     GroqSTTBackend instance.
        llm_chain:       LLMChain (Groq primary → Gemini fallback).
        tts_router:      TTSRouter for sentence-boundary TTS synthesis.
        lang_detector:   LanguageDetector to map STT language codes.
        prompt_builder:  PromptBuilder to assemble LLM message lists.
        intent_classifier: Optional IntentClassifier (None if Tavily not configured).
        tavily_client:   Optional TavilySearchClient (None if not configured).
        broadcast_fn:    Async callable that sends a JSON dict to all BrowserUI clients.
    """

    def __init__(
        self,
        audio_client_ws: WebSocket,
        state: PipelineState,
        stt_backend: GroqSTTBackend,
        llm_chain: LLMChain,
        tts_router: TTSRouter,
        lang_detector: LanguageDetector,
        prompt_builder: PromptBuilder,
        intent_classifier: Optional[IntentClassifier],
        tavily_client: Optional[TavilySearchClient],
        broadcast_fn: Callable,
    ) -> None:
        self._audio_ws = audio_client_ws
        self._state = state
        self._stt = stt_backend
        self._llm = llm_chain
        self._tts = tts_router
        self._lang = lang_detector
        self._prompt = prompt_builder
        self._intent = intent_classifier
        self._tavily = tavily_client
        self._broadcast_fn = broadcast_fn

        self._tasks: list[asyncio.Task] = []

        # Per-turn timing / token counters (reset each turn)
        self._speech_end_time: float = 0.0
        self._first_token_time: float = 0.0
        self._token_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Spawn all five workers as asyncio tasks and await them."""
        self._tasks = [
            asyncio.create_task(self._audio_input_worker(), name="audio_input_worker"),
            asyncio.create_task(self._stt_worker(), name="stt_worker"),
            asyncio.create_task(self._llm_worker(), name="llm_worker"),
            asyncio.create_task(self._tts_worker(), name="tts_worker"),
            asyncio.create_task(self._audio_output_worker(), name="audio_output_worker"),
        ]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("VoicePipeline.run() unhandled exception: %s", exc, exc_info=True)

    def stop(self) -> None:
        """Cancel all pipeline worker tasks."""
        for task in self._tasks:
            if not task.done():
                task.cancel()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected BrowserUI clients."""
        try:
            await self._broadcast_fn(message)
        except Exception as exc:
            logger.warning("_broadcast failed: %s", exc)

    async def _set_state(self, new_state: str, message: str = "") -> None:
        """Update pipeline state and broadcast status to all clients."""
        old_state = self._state.state
        self._state.state = new_state
        logger.debug(
            "state transition: %s → %s (t=%.3fs)",
            old_state,
            new_state,
            time.monotonic(),
        )
        status_msg: dict = {"type": "status", "state": new_state}
        if message:
            status_msg["message"] = message
        # Broadcast to all BrowserUI clients
        await self._broadcast(status_msg)
        # Also send to AudioClient
        try:
            await self._audio_ws.send_text(json.dumps(status_msg))
        except Exception as exc:
            logger.warning("_set_state: failed to send to AudioClient: %s", exc)

    # ------------------------------------------------------------------
    # Worker 1: audio_input_worker
    # ------------------------------------------------------------------

    async def _audio_input_worker(self) -> None:
        """Receive binary PCM16 frames (or JSON interrupt) from the AudioClient WebSocket.

        - Binary frames are pushed to state.audio_queue.
        - JSON frames with type=="interrupt" set state.interrupt = True.
        - Runs until the WebSocket disconnects, then cancels all other workers.
        """
        try:
            while True:
                try:
                    # Receive the next WebSocket message (binary or text)
                    data = await self._audio_ws.receive()
                except WebSocketDisconnect:
                    logger.info(
                        "audio_input_worker: AudioClient disconnected (session=%s)",
                        self._state.session_id,
                    )
                    break
                except Exception as exc:
                    logger.error("audio_input_worker: receive error: %s", exc)
                    break

                # Starlette WebSocket.receive() returns a dict with either
                # "bytes" or "text" key (plus "type").
                msg_type = data.get("type", "")
                if msg_type == "websocket.disconnect":
                    logger.info(
                        "audio_input_worker: WebSocket disconnect event (session=%s)",
                        self._state.session_id,
                    )
                    break

                raw_bytes = data.get("bytes")
                raw_text = data.get("text")

                if raw_bytes is not None:
                    # Try to parse as JSON first (interrupt messages may arrive as binary)
                    try:
                        parsed = json.loads(raw_bytes)
                        if parsed.get("type") == "interrupt":
                            logger.debug(
                                "audio_input_worker: interrupt received (binary JSON)"
                            )
                            self._state.interrupt = True
                            continue
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                    # Regular PCM16 audio frame
                    await self._state.audio_queue.put(raw_bytes)

                elif raw_text is not None:
                    # JSON control message
                    try:
                        parsed = json.loads(raw_text)
                        if parsed.get("type") == "interrupt":
                            logger.debug(
                                "audio_input_worker: interrupt received (text JSON)"
                            )
                            self._state.interrupt = True
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "audio_input_worker: failed to parse JSON text: %s", exc
                        )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "audio_input_worker: unexpected error: %s", exc, exc_info=True
            )
        finally:
            # Cancel all sibling workers when the WebSocket disconnects
            logger.info(
                "audio_input_worker: shutting down pipeline (session=%s)",
                self._state.session_id,
            )
            self.stop()

    # ------------------------------------------------------------------
    # Worker 2: stt_worker
    # ------------------------------------------------------------------

    async def _stt_worker(self) -> None:
        """Drain audio_queue, transcribe via Groq STT, push TranscriptionResult.

        State transition: listening → thinking
        """
        try:
            while True:
                try:
                    pcm16_bytes: bytes = await asyncio.wait_for(
                        self._state.audio_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                logger.debug(
                    "stt_worker: received %d bytes of PCM16 audio", len(pcm16_bytes)
                )

                # Record speech_end time for TTFA measurement
                self._speech_end_time = time.monotonic()
                logger.debug(
                    "stt_worker: speech_end recorded (audio_queue depth=%d)",
                    self._state.audio_queue.qsize(),
                )

                # Transition to thinking
                await self._set_state("thinking")

                try:
                    result: TranscriptionResult = await self._stt.transcribe(pcm16_bytes)
                except Exception as exc:
                    logger.error("stt_worker: STT transcription failed: %s", exc)
                    # Send user-friendly error and return to listening
                    await self._set_state(
                        "listening",
                        message="Couldn't hear you clearly. Please try again.",
                    )
                    continue

                logger.debug(
                    "stt_worker: transcript=%r language=%r",
                    result.text,
                    result.language,
                )

                # Update detected language
                self._state.detected_language = self._lang.detect(result)

                # Broadcast transcript to BrowserUI
                await self._broadcast(
                    {
                        "type": "transcript",
                        "text": result.text,
                        "language": result.language,
                    }
                )

                # Push to transcript queue for llm_worker
                await self._state.transcript_queue.put(result)
                logger.debug(
                    "stt_worker: transcript_queue depth=%d",
                    self._state.transcript_queue.qsize(),
                )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("stt_worker: unexpected error: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Worker 3: llm_worker
    # ------------------------------------------------------------------

    async def _llm_worker(self) -> None:
        """Drain transcript_queue, run intent check, stream LLM tokens.

        - Optionally calls Tavily search for SEARCH intent.
        - Streams tokens to token_queue and broadcasts llm_text_chunk to BrowserUI.
        - Respects state.interrupt to stop streaming early.
        - Appends user/assistant turn to conversation history after each turn.
        - Enforces 10-turn sliding window on history.
        """
        try:
            while True:
                try:
                    transcript: TranscriptionResult = await asyncio.wait_for(
                        self._state.transcript_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                logger.debug("llm_worker: processing transcript=%r", transcript.text)

                # Optional intent classification + Tavily search
                search_context = ""
                if self._intent is not None and self._tavily is not None:
                    intent = self._intent.classify(transcript.text)
                    logger.debug("llm_worker: intent=%r", intent)
                    if intent == "SEARCH":
                        try:
                            search_context = await self._tavily.search(transcript.text)
                            logger.debug(
                                "llm_worker: search_context length=%d",
                                len(search_context),
                            )
                        except Exception as exc:
                            logger.warning(
                                "llm_worker: Tavily search failed: %s — continuing as GENERAL",
                                exc,
                            )
                            search_context = ""

                # Build messages for LLM
                messages = self._prompt.build(
                    transcript.text,
                    self._state.history,
                    search_context,
                )

                # Stream tokens from LLM
                full_response_parts: list[str] = []
                self._token_count = 0
                try:
                    async for token in self._llm.stream(messages):
                        # Check interrupt flag before processing each token
                        if self._state.interrupt:
                            logger.debug("llm_worker: interrupt detected, stopping stream")
                            break

                        # Track first-token latency
                        if not full_response_parts:
                            self._first_token_time = time.monotonic()
                            logger.debug(
                                "llm_worker: first token latency=%.0fms",
                                (self._first_token_time - self._speech_end_time) * 1000,
                            )

                        full_response_parts.append(token)
                        self._token_count += 1

                        # Push token to tts_worker
                        await self._state.token_queue.put(token)
                        logger.debug(
                            "llm_worker: token_queue depth=%d",
                            self._state.token_queue.qsize(),
                        )

                        # Broadcast to BrowserUI
                        await self._broadcast(
                            {"type": "llm_text_chunk", "text": token}
                        )
                except Exception as exc:
                    logger.error(
                        "llm_worker: LLM streaming error: %s", exc, exc_info=True
                    )

                logger.debug("llm_worker: total_tokens=%d", self._token_count)
                self._token_count = 0

                # Signal end of token stream to tts_worker
                await self._state.token_queue.put(_END_OF_TOKENS)

                # Update conversation history (even if interrupted, save what we have)
                full_response = "".join(full_response_parts)
                if transcript.text.strip() and full_response.strip():
                    self._state.history.append(
                        {"role": "user", "content": transcript.text}
                    )
                    self._state.history.append(
                        {"role": "assistant", "content": full_response}
                    )
                    # Enforce 10-turn (20-entry) sliding window
                    while len(self._state.history) > _MAX_HISTORY_ENTRIES:
                        # Remove oldest user+assistant pair
                        self._state.history.pop(0)
                        self._state.history.pop(0)

                    logger.debug(
                        "llm_worker: history now %d entries", len(self._state.history)
                    )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("llm_worker: unexpected error: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Worker 4: tts_worker
    # ------------------------------------------------------------------

    async def _tts_worker(self) -> None:
        """Drain token_queue, accumulate to sentence boundary, synthesise WAV.

        - Uses TTSRouter.accumulate() to detect sentence boundaries.
        - Calls TTSRouter.synthesize() when a sentence is complete.
        - Pushes WAV bytes to audio_out_queue.
        - Respects state.interrupt: drains and discards audio_out_queue.
        - Calls TTSRouter.flush() at end of token stream for any remaining text.
        """
        try:
            while True:
                try:
                    token = await asyncio.wait_for(
                        self._state.token_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Check interrupt before processing
                if self._state.interrupt:
                    logger.debug("tts_worker: interrupt detected, draining queues")
                    await self._handle_interrupt()
                    # Drain remaining tokens until end-of-stream sentinel
                    if token is not _END_OF_TOKENS:
                        await self._drain_token_queue()
                    continue

                # End-of-stream sentinel
                if token is _END_OF_TOKENS:
                    # Flush any remaining buffered text
                    remaining = self._tts.flush()
                    if remaining and not self._state.interrupt:
                        await self._synthesize_and_enqueue(remaining)
                    continue

                # Accumulate token; check for sentence boundary
                sentence = self._tts.accumulate(token)
                if sentence and not self._state.interrupt:
                    await self._synthesize_and_enqueue(sentence)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("tts_worker: unexpected error: %s", exc, exc_info=True)

    async def _synthesize_and_enqueue(self, sentence: str) -> None:
        """Synthesise a sentence and push WAV bytes to audio_out_queue."""
        try:
            wav_bytes = await self._tts.synthesize(
                sentence, self._state.detected_language
            )
            await self._state.audio_out_queue.put(wav_bytes)
            logger.debug(
                "tts_worker: synthesised %d bytes for sentence=%r (audio_out_queue depth=%d)",
                len(wav_bytes),
                sentence,
                self._state.audio_out_queue.qsize(),
            )
        except Exception as exc:
            logger.error(
                "tts_worker: synthesis failed for sentence=%r: %s", sentence, exc
            )
            # Skip failed chunk; pipeline continues (Requirement 9.2)

    async def _drain_token_queue(self) -> None:
        """Drain all remaining tokens from token_queue until the end-of-stream sentinel."""
        while True:
            try:
                token = await asyncio.wait_for(
                    self._state.token_queue.get(), timeout=1.0
                )
                if token is _END_OF_TOKENS:
                    break
            except asyncio.TimeoutError:
                break

    async def _handle_interrupt(self) -> None:
        """Drain and discard audio_out_queue on interrupt."""
        drained = 0
        while not self._state.audio_out_queue.empty():
            try:
                self._state.audio_out_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.debug("tts_worker: drained %d WAV chunks from audio_out_queue", drained)
        # Also flush the TTS buffer
        self._tts.flush()

    # ------------------------------------------------------------------
    # Worker 5: audio_output_worker
    # ------------------------------------------------------------------

    async def _audio_output_worker(self) -> None:
        """Drain audio_out_queue and send WAV bytes to the AudioClient.

        State transitions:
          thinking → speaking  on first WAV chunk
          speaking → listening when queue is empty after all tokens processed
          speaking → listening on interrupt
        """
        try:
            first_chunk = True
            while True:
                # Check interrupt flag
                if self._state.interrupt:
                    logger.debug(
                        "audio_output_worker: interrupt detected, returning to listening"
                    )
                    await self._set_state("listening")
                    self._state.interrupt = False
                    first_chunk = True
                    # Drain any remaining audio
                    while not self._state.audio_out_queue.empty():
                        try:
                            self._state.audio_out_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    await asyncio.sleep(0.05)
                    continue

                try:
                    wav_bytes: bytes = await asyncio.wait_for(
                        self._state.audio_out_queue.get(), timeout=0.1
                    )
                except asyncio.TimeoutError:
                    # Queue is empty — if we were speaking, transition to listening
                    if self._state.state == "speaking":
                        logger.debug(
                            "audio_output_worker: audio_out_queue empty, transitioning to listening"
                        )
                        await self._set_state("listening")
                        first_chunk = True
                    continue

                # On first WAV chunk: transition thinking → speaking
                if first_chunk:
                    ttfa_ms = (time.monotonic() - self._speech_end_time) * 1000
                    logger.debug(
                        "TTFA: %.0fms (speech_end → first_audio)", ttfa_ms
                    )
                    logger.debug(
                        "audio_output_worker: first WAV chunk received, transitioning to speaking"
                    )
                    await self._set_state("speaking")
                    first_chunk = False

                # Send WAV bytes to AudioClient
                try:
                    await self._audio_ws.send_bytes(wav_bytes)
                    logger.debug(
                        "audio_output_worker: sent %d WAV bytes to AudioClient",
                        len(wav_bytes),
                    )
                except Exception as exc:
                    logger.error(
                        "audio_output_worker: failed to send WAV bytes: %s", exc
                    )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "audio_output_worker: unexpected error: %s", exc, exc_info=True
            )
