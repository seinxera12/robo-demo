"""
Voice pipeline for the Lightweight Voice Demo server.

Contains PipelineState (the shared state for one WebSocket session) and
VoicePipeline (the five asyncio worker coroutines).

Workers:
  audio_input_worker  — receives PCM16 from AudioClient WebSocket
  stt_worker          — transcribes audio via Groq Whisper API (only when robo_active)
  llm_worker          — streams LLM tokens, optional Tavily search
  tts_worker          — accumulates tokens to sentence boundary, synthesises WAV
  audio_output_worker — sends WAV chunks to AudioClient, manages state transitions
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from server.lang.detector import LanguageDetector
from server.llm.assembler import DeploymentConfig, PromptAssembler
from server.llm.chain import LLMChain
from server.llm.intent import IntentClassifier
from server.llm.postprocess import PostProcessor
from server.llm.router import Router
from server.log import pipeline_error, pipeline_event, pipeline_separator, pipeline_warn, tts_log
from server.models import TranscriptionResult
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
    """Shared mutable state for a single AudioClient WebSocket session."""

    session_id: str
    history: list[dict]              # ConversationHistory, max 10 turns
    state: str                       # "listening" | "thinking" | "speaking"
    interrupt: bool                  # barge-in flag
    detected_language: str           # "en" | "ja", default "en"
    audio_queue: asyncio.Queue       # PCM16 bytes from AudioClient
    transcript_queue: asyncio.Queue  # TranscriptionResult
    token_queue: asyncio.Queue       # str tokens from LLM (or _END_OF_TOKENS sentinel)
    audio_out_queue: asyncio.Queue   # WAV bytes for AudioClient
    robo_active: bool = False        # True when the user has activated Robo via the UI button
    deployment_config: DeploymentConfig = field(default_factory=DeploymentConfig)  # Deployment configuration
    model_tier: str = "groq"         # "groq" | "small", determined at startup


# ---------------------------------------------------------------------------
# VoicePipeline
# ---------------------------------------------------------------------------


class VoicePipeline:
    """Orchestrates the five asyncio pipeline workers for a single session."""

    def __init__(
        self,
        audio_client_ws: WebSocket,
        state: PipelineState,
        stt_backend: GroqSTTBackend,
        llm_chain: LLMChain,
        tts_router: TTSRouter,
        lang_detector: LanguageDetector,
        intent_classifier: IntentClassifier,
        prompt_assembler: PromptAssembler,
        router: Router,
        post_processor: PostProcessor,
        broadcast_fn: Callable,
    ) -> None:
        self._audio_ws = audio_client_ws
        self._state = state
        self._stt = stt_backend
        self._llm = llm_chain
        self._tts = tts_router
        self._lang = lang_detector
        self._intent = intent_classifier
        self._assembler = prompt_assembler
        self._router = router
        self._post_processor = post_processor
        self._broadcast_fn = broadcast_fn

        self._tasks: list[asyncio.Task] = []

        # Per-turn timing / token counters (reset each turn)
        self._speech_end_time: float = 0.0
        self._token_count: int = 0
        self._turn_audio_duration_ms: int = 0   # total audio queued this turn (ms)
        self._tts_turn_complete: bool = False    # set True when tts_worker finishes a turn

        # Throttle the "audio_dropped_robo_idle" log — only emit once per 60s
        self._last_idle_drop_log: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Spawn all five workers as asyncio tasks and await them."""
        sid = self._state.session_id[:8]
        pipeline_separator(f"SESSION {sid}")
        pipeline_event("SESSION", "started", session=sid)
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
            pipeline_error("SESSION", "unhandled_exception", session=sid, error=str(exc))
            logger.error("VoicePipeline.run() unhandled exception: %s", exc, exc_info=True)

    def stop(self) -> None:
        """Cancel all pipeline worker tasks."""
        sid = self._state.session_id[:8]
        pipeline_event("SESSION", "stopped", session=sid)
        pipeline_separator()
        for task in self._tasks:
            if not task.done():
                task.cancel()

    def set_robo_active(self, active: bool) -> None:
        """Called by the WebSocket handler when the UI button is toggled."""
        self._state.robo_active = active
        pipeline_event("ROBO", "activated" if active else "deactivated",
                       session=self._state.session_id[:8])

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

        pipeline_event("STATE", f"{old_state} → {new_state}",
                       session=self._state.session_id[:8],
                       **({"msg": message} if message else {}))

        status_msg: dict = {"type": "status", "state": new_state}
        if message:
            status_msg["message"] = message
        await self._broadcast(status_msg)
        try:
            await self._audio_ws.send_text(json.dumps(status_msg))
        except Exception as exc:
            pipeline_warn("STATE", "send_failed",
                          session=self._state.session_id[:8], error=str(exc))

        # After a full turn (speaking → listening), deactivate Robo so the
        # user must press the button again for the next turn.
        if new_state == "listening" and old_state == "speaking" and self._state.robo_active:
            self._state.robo_active = False
            pipeline_event("ROBO", "auto_deactivated_after_turn",
                           session=self._state.session_id[:8])
            await self._broadcast({"type": "robo_deactivated"})

    # ------------------------------------------------------------------
    # Worker 1: audio_input_worker
    # ------------------------------------------------------------------

    async def _audio_input_worker(self) -> None:
        """Receive binary PCM16 frames (or JSON interrupt) from the AudioClient WebSocket."""
        sid = self._state.session_id[:8]
        try:
            while True:
                try:
                    data = await self._audio_ws.receive()
                except WebSocketDisconnect:
                    pipeline_event("AUDIO_IN", "client_disconnected", session=sid)
                    break
                except Exception as exc:
                    pipeline_error("AUDIO_IN", "receive_error", session=sid, error=str(exc))
                    break

                msg_type = data.get("type", "")
                if msg_type == "websocket.disconnect":
                    pipeline_event("AUDIO_IN", "client_disconnected", session=sid)
                    break

                raw_bytes = data.get("bytes")
                raw_text = data.get("text")

                if raw_bytes is not None:
                    try:
                        parsed = json.loads(raw_bytes)
                        if parsed.get("type") == "interrupt":
                            pipeline_event("AUDIO_IN", "interrupt_received",
                                           session=sid, via="binary")
                            self._state.interrupt = True
                            continue
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                    await self._state.audio_queue.put(raw_bytes)

                elif raw_text is not None:
                    try:
                        parsed = json.loads(raw_text)
                        if parsed.get("type") == "interrupt":
                            pipeline_event("AUDIO_IN", "interrupt_received",
                                           session=sid, via="text")
                            self._state.interrupt = True
                    except json.JSONDecodeError as exc:
                        pipeline_warn("AUDIO_IN", "bad_json", session=sid, error=str(exc))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pipeline_error("AUDIO_IN", "unexpected_error", session=sid, error=str(exc))
        finally:
            pipeline_event("AUDIO_IN", "shutting_down_pipeline", session=sid)
            self.stop()

    # ------------------------------------------------------------------
    # Worker 2: stt_worker
    # ------------------------------------------------------------------

    async def _stt_worker(self) -> None:
        """Drain audio_queue and transcribe — only when robo_active is True.

        Audio frames that arrive while robo_active is False are silently
        discarded; the mic keeps running but no STT API calls are made.
        """
        sid = self._state.session_id[:8]
        try:
            while True:
                try:
                    pcm16_bytes: bytes = await asyncio.wait_for(
                        self._state.audio_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Gate: drop audio silently when Robo is not active.
                # Log at most once per 60s to avoid flooding system.log.
                if not self._state.robo_active:
                    now = time.monotonic()
                    if now - self._last_idle_drop_log >= 60.0:
                        pipeline_event("STT", "audio_dropped_robo_idle", session=sid)
                        self._last_idle_drop_log = now
                    continue

                self._speech_end_time = time.monotonic()
                audio_kb = len(pcm16_bytes) // 1024
                pipeline_event("STT", "audio_received", session=sid, size_kb=audio_kb)

                await self._set_state("thinking")

                try:
                    result: TranscriptionResult = await self._stt.transcribe(pcm16_bytes)
                except Exception as exc:
                    pipeline_error("STT", "transcription_failed", session=sid, error=str(exc))
                    await self._set_state("listening")
                    continue

                stt_ms = int((time.monotonic() - self._speech_end_time) * 1000)
                pipeline_event("STT", "transcript_ready",
                               session=sid,
                               text=result.text[:80],
                               lang=result.language,
                               latency_ms=stt_ms)

                self._state.detected_language = self._lang.detect(result)

                await self._broadcast({
                    "type": "transcript",
                    "text": result.text,
                    "language": result.language,
                })

                pipeline_event("STT", "forwarded_to_llm",
                               session=sid,
                               text=result.text[:80],
                               lang=self._state.detected_language)

                await self._state.transcript_queue.put(result)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pipeline_error("STT", "unexpected_error", session=sid, error=str(exc))

    # ------------------------------------------------------------------
    # Worker 3: llm_worker
    # ------------------------------------------------------------------

    async def _llm_worker(self) -> None:
        """Drain transcript_queue, run intent classification, route, assemble prompt, stream LLM tokens."""
        sid = self._state.session_id[:8]

        # Fallback message used when Call 2 returns empty after retry
        _FALLBACK_MESSAGE = (
            "I'm having trouble connecting right now. Please try again in a moment."
        )

        try:
            while True:
                try:
                    transcript: TranscriptionResult = await asyncio.wait_for(
                        self._state.transcript_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Task 13.6 — Empty transcript skip logic
                if transcript.text.strip() == "":
                    logger.info("LLM: empty transcript — skipping LLM calls (session=%s)", sid)
                    await self._set_state("listening")
                    continue

                pipeline_event("LLM", "turn_start",
                               session=sid,
                               query=transcript.text[:80],
                               history_turns=len(self._state.history) // 2)

                # Reset per-turn counters
                self._turn_audio_duration_ms = 0
                self._tts_turn_complete = False

                # Task 17.1 — Unhandled exception wrapper for the entire per-turn block
                try:

                    # Task 13.1 — Call 1: LLM-based intent classification
                    call1_start = time.monotonic()
                    intent_result = await self._intent.classify(transcript.text)
                    call1_ttft_ms = int((time.monotonic() - call1_start) * 1000)
                    pipeline_event("LLM", "call1_intent_classified",
                                   session=sid,
                                   intent=intent_result.intent,
                                   language=intent_result.language,
                                   confidence=intent_result.confidence,
                                   ttft_ms=call1_ttft_ms)

                    # Task 13.2 — Route based on intent result
                    route_result = await self._router.route(
                        intent_result, self._state.detected_language
                    )
                    pipeline_event("LLM", "routed",
                                   session=sid,
                                   route_type=route_result.route_type,
                                   has_direct_response=route_result.direct_response is not None,
                                   has_context=bool(route_result.retrieved_context))

                    # Task 13.2 — If direct_response is set: skip Call 2
                    if route_result.direct_response is not None:
                        direct_text = route_result.direct_response
                        pipeline_event("LLM", "direct_response",
                                       session=sid,
                                       route_type=route_result.route_type,
                                       response_preview=direct_text[:60])
                        # Push the direct response as a single token + sentinel
                        await self._state.token_queue.put(direct_text)
                        await self._broadcast({"type": "llm_text_chunk", "text": direct_text})
                        await self._state.token_queue.put(_END_OF_TOKENS)

                        # Store in history (direct responses are not truncated — they're short)
                        if transcript.text.strip() and direct_text.strip():
                            self._state.history.append(
                                {"role": "user", "content": transcript.text}
                            )
                            self._state.history.append(
                                {"role": "assistant", "content": direct_text}
                            )
                            while len(self._state.history) > _MAX_HISTORY_ENTRIES:
                                self._state.history.pop(0)
                                self._state.history.pop(0)
                        continue

                    # Task 13.3 — Assemble prompt using PromptAssembler
                    _system_prompt, messages = self._assembler.assemble_prompt(
                        user_input=transcript.text,
                        intent_result=intent_result,
                        session_history=self._state.history,
                        retrieved_context=route_result.retrieved_context,
                        route_type=route_result.route_type,
                    )

                    # Task 13.5 — Call 2: stream main response with retry logic
                    async def _run_call2() -> str:
                        """Run Call 2 and collect full response. Returns empty string on failure."""
                        parts: list[str] = []
                        token_count = 0
                        first_token_logged = False
                        try:
                            async for token in self._llm.stream(
                                messages, max_tokens=200, temperature=0.65
                            ):
                                if self._state.interrupt:
                                    pipeline_event("LLM", "stream_interrupted",
                                                   session=sid, tokens_so_far=token_count)
                                    break

                                if not first_token_logged:
                                    ttft_ms = int(
                                        (time.monotonic() - self._speech_end_time) * 1000
                                    )
                                    pipeline_event("LLM", "call2_first_token",
                                                   session=sid, ttft_ms=ttft_ms)
                                    first_token_logged = True

                                parts.append(token)
                                token_count += 1
                        except Exception as exc:
                            pipeline_error("LLM", "call2_stream_error",
                                           session=sid, error=str(exc))
                        return "".join(parts)

                    full_response = await _run_call2()

                    # Retry once if empty
                    if not full_response.strip():
                        pipeline_warn("LLM", "call2_empty_response_retrying", session=sid)
                        full_response = await _run_call2()

                    # After retry failure: use fallback message
                    if not full_response.strip():
                        pipeline_error("LLM", "call2_empty_after_retry_using_fallback",
                                       session=sid)
                        full_response = _FALLBACK_MESSAGE

                    # Task 13.2 — Append clarification suffix if set
                    if route_result.clarification_suffix:
                        full_response = full_response + " " + route_result.clarification_suffix

                    # Task 13.4 — Apply PostProcessor before pushing to TTS
                    cleaned_response = self._post_processor.clean(
                        full_response, self._state.detected_language
                    )

                    pipeline_event("LLM", "turn_complete",
                                   session=sid,
                                   response_preview=cleaned_response[:60])

                    # Push cleaned response as a single token + sentinel to TTS worker
                    await self._state.token_queue.put(cleaned_response)
                    await self._broadcast({"type": "llm_text_chunk", "text": cleaned_response})
                    await self._state.token_queue.put(_END_OF_TOKENS)

                    # Task 13.7 — Store history with long response truncation
                    if transcript.text.strip() and full_response.strip():
                        # Truncate assistant response to 120 tokens (words) before storing
                        response_words = full_response.split()
                        if len(response_words) > 120:
                            stored_response = " ".join(response_words[:120])
                        else:
                            stored_response = full_response

                        self._state.history.append(
                            {"role": "user", "content": transcript.text}
                        )
                        self._state.history.append(
                            {"role": "assistant", "content": stored_response}
                        )
                        # Enforce _MAX_HISTORY_ENTRIES cap (remove oldest pairs first)
                        while len(self._state.history) > _MAX_HISTORY_ENTRIES:
                            self._state.history.pop(0)
                            self._state.history.pop(0)

                except Exception as exc:
                    # Task 17.1 — Catch unhandled exceptions, log ERROR, emit fallback
                    pipeline_error("LLM", "turn_unhandled_exception",
                                   session=sid, error=str(exc))
                    logger.error(
                        "LLM worker unhandled exception (session=%s): %s",
                        sid, exc, exc_info=True,
                    )
                    try:
                        await self._state.token_queue.put(_FALLBACK_MESSAGE)
                        await self._broadcast(
                            {"type": "llm_text_chunk", "text": _FALLBACK_MESSAGE}
                        )
                        await self._state.token_queue.put(_END_OF_TOKENS)
                    except Exception:
                        pass  # Don't let queue errors propagate either

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pipeline_error("LLM", "unexpected_error", session=sid, error=str(exc))

    # ------------------------------------------------------------------
    # Worker 4: tts_worker
    # ------------------------------------------------------------------

    async def _tts_worker(self) -> None:
        """Drain token_queue, accumulate to sentence boundary, synthesise WAV."""
        sid = self._state.session_id[:8]
        try:
            while True:
                try:
                    token = await asyncio.wait_for(
                        self._state.token_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                if self._state.interrupt:
                    pipeline_event("TTS", "interrupt_drain", session=sid)
                    await self._handle_interrupt()
                    if token is not _END_OF_TOKENS:
                        await self._drain_token_queue()
                    continue

                if token is _END_OF_TOKENS:
                    remaining = self._tts.flush()
                    if remaining and not self._state.interrupt:
                        await self._synthesize_and_enqueue(remaining)
                    # Signal to audio_output_worker that all sentences for this
                    # turn have been synthesised and enqueued.
                    self._tts_turn_complete = True
                    continue

                sentence = self._tts.accumulate(token)
                if sentence and not self._state.interrupt:
                    await self._synthesize_and_enqueue(sentence)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pipeline_error("TTS", "unexpected_error", session=sid, error=str(exc))

    async def _synthesize_and_enqueue(self, sentence: str) -> None:
        """Synthesise a sentence and push WAV bytes to audio_out_queue."""
        sid = self._state.session_id[:8]
        try:
            wav_bytes = await self._tts.synthesize(sentence, self._state.detected_language)
            if not wav_bytes:
                tts_log.warning("empty_wav  session=%s  sentence=%r", sid, sentence[:60])
                return
            # Estimate duration: WAV body = total_bytes - 44 byte header, 16-bit mono 24kHz
            wav_body_bytes = max(0, len(wav_bytes) - 44)
            duration_ms = int(wav_body_bytes / 2 / 24000 * 1000)
            self._turn_audio_duration_ms += duration_ms
            await self._state.audio_out_queue.put(wav_bytes)
            pipeline_event("TTS", "synthesised",
                           session=sid,
                           sentence=sentence[:60],
                           wav_kb=len(wav_bytes) // 1024)
            tts_log.info(
                "enqueued  session=%s  sentence=%r  wav_bytes=%d  "
                "duration_ms=%d  turn_total_ms=%d",
                sid, sentence[:60], len(wav_bytes), duration_ms,
                self._turn_audio_duration_ms,
            )
        except Exception as exc:
            pipeline_error("TTS", "synthesis_failed",
                           session=sid, sentence=sentence[:60], error=str(exc))

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
            pipeline_event("TTS", "queue_drained",
                           session=self._state.session_id[:8], chunks=drained)
        self._tts.flush()

    # ------------------------------------------------------------------
    # Worker 5: audio_output_worker
    # ------------------------------------------------------------------

    async def _audio_output_worker(self) -> None:
        """Drain audio_out_queue and send WAV bytes to the AudioClient."""
        sid = self._state.session_id[:8]
        try:
            first_chunk = True
            chunk_send_time: float = 0.0

            while True:
                if self._state.interrupt:
                    pipeline_event("AUDIO_OUT", "interrupt_clear", session=sid)
                    await self._set_state("listening")
                    self._state.interrupt = False
                    first_chunk = True
                    self._turn_audio_duration_ms = 0
                    self._tts_turn_complete = False
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
                    # Queue is empty — only consider playback done when:
                    # 1. tts_worker has finished synthesising all sentences, AND
                    # 2. enough wall-clock time has passed for the client to play them.
                    if self._state.state == "speaking" and self._tts_turn_complete:
                        elapsed_since_last_send_ms = int(
                            (time.monotonic() - chunk_send_time) * 1000
                        )
                        wait_ms = self._turn_audio_duration_ms + 300
                        if elapsed_since_last_send_ms >= wait_ms:
                            pipeline_event("AUDIO_OUT", "playback_complete",
                                           session=sid,
                                           audio_duration_ms=self._turn_audio_duration_ms,
                                           waited_ms=elapsed_since_last_send_ms)
                            tts_log.info(
                                "playback_complete  session=%s  audio_duration_ms=%d  "
                                "waited_ms=%d",
                                sid, self._turn_audio_duration_ms, elapsed_since_last_send_ms,
                            )
                            self._turn_audio_duration_ms = 0
                            self._tts_turn_complete = False
                            await self._set_state("listening")
                            first_chunk = True
                    continue

                if first_chunk:
                    ttfa_ms = int((time.monotonic() - self._speech_end_time) * 1000)
                    pipeline_event("AUDIO_OUT", "first_audio_chunk",
                                   session=sid, ttfa_ms=ttfa_ms)
                    tts_log.info("first_chunk_sent  session=%s  ttfa_ms=%d  wav_bytes=%d",
                                 sid, ttfa_ms, len(wav_bytes))
                    await self._set_state("speaking")
                    first_chunk = False

                try:
                    await self._audio_ws.send_bytes(wav_bytes)
                    chunk_send_time = time.monotonic()
                    tts_log.debug("chunk_sent  session=%s  wav_bytes=%d", sid, len(wav_bytes))
                except Exception as exc:
                    pipeline_error("AUDIO_OUT", "send_failed",
                                   session=sid, error=str(exc))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pipeline_error("AUDIO_OUT", "unexpected_error", session=sid, error=str(exc))
