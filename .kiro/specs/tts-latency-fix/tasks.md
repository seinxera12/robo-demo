# Implementation Plan: TTS Latency Fix

## Overview

Implement five targeted changes across three files to reduce Time-to-First-Audio from ~10 s to under 3 s on warm sessions. Changes are ordered so each builds on the previous and the pipeline remains runnable after each step.

## Tasks

- [x] 1. Add `warm_up()` to `KokoroTTS` and `KokoroJapaneseTTS`
  - In `server/tts/kokoro_tts.py`, add a synchronous `warm_up(self) -> None` method to `KokoroTTS` that calls `self._get_pipeline()` and logs `warm_up_complete engine=KokoroTTS` via `tts_log.info`.
  - Add the same method to `KokoroJapaneseTTS`, logging `warm_up_complete engine=KokoroJapaneseTTS`.
  - Both methods are synchronous (not async) — they are called via `run_in_executor`.
  - _Requirements: 3.4, 3.5_

  - [ ]* 1.1 Write unit tests for `warm_up()`
    - Mock `_get_pipeline` to verify it is called exactly once per `warm_up()` invocation.
    - Verify `warm_up()` returns `None` (no audio bytes produced).
    - Verify `_pipeline` is non-None after `warm_up()` completes.
    - **Property 8: warm_up loads pipeline without producing audio**
    - **Validates: Requirements 3.4, 3.5**

- [x] 2. Update `server/main.py` to warm up both engines concurrently at startup
  - Replace the existing pre-warm block (which calls `kokoro_tts._synthesize_sync("Hello.")`) with an `asyncio.gather` call that runs `kokoro_tts.warm_up` and `kokoro_ja_tts.warm_up` concurrently via `loop.run_in_executor`.
  - Log `"Pre-warming KokoroTTS and KokoroJapaneseTTS..."` before the gather.
  - Log `"Both Kokoro TTS engines pre-warmed successfully."` after successful gather (Requirement 3.6).
  - Wrap in `try/except Exception` and log a warning on failure (Requirement 3.3); server must continue starting up.
  - _Requirements: 3.1, 3.2, 3.3, 3.6_

  - [ ]* 2.1 Write unit tests for startup warm-up
    - Mock `kokoro_tts.warm_up` and `kokoro_ja_tts.warm_up`; verify both are called during lifespan startup.
    - Verify that if `warm_up` raises, the server lifespan continues (no re-raise).
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Checkpoint — verify existing tests still pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Refactor `_synthesize_and_enqueue` to return WAV bytes and accept `sentence_index`
  - Change the signature to `async def _synthesize_and_enqueue(self, sentence: str, sentence_index: int) -> tuple[bytes, int] | None`.
  - Apply `PostProcessor.clean(sentence, self._state.detected_language)` to the sentence before calling `self._tts.synthesize()`. If the cleaned sentence is empty, return `None`.
  - Remove the `audio_out_queue.put()` call from this method — the caller will enqueue in order.
  - Log `ttfs_ms` (time since `self._speech_end_time`) via `pipeline_event("TTS", "first_sentence_synthesis_start", ttfs_ms=ttfs_ms)` when `sentence_index == 0`, before synthesis starts.
  - Log `synth_ms` (synthesis wall-clock time), `sentence_index`, and `wav_kb` via `pipeline_event("TTS", "synthesised", ...)` after synthesis completes.
  - Return `(wav_bytes, sentence_index)` on success, `None` if synthesis returns empty bytes.
  - _Requirements: 1.6 (Option A), 4.1, 4.3_

  - [ ]* 4.1 Write unit tests for `_synthesize_and_enqueue`
    - Mock `self._tts.synthesize` and `self._post_processor.clean`.
    - Verify `clean` is called with the raw sentence and `detected_language` before `synthesize`.
    - Verify return value is `(wav_bytes, sentence_index)` on success.
    - Verify return value is `None` when `clean` returns empty string.
    - Verify return value is `None` when `synthesize` returns empty bytes.
    - Verify `ttfs_ms` is logged only for `sentence_index == 0`.
    - **Property 4: Per-sentence cleaning round-trip**
    - **Validates: Requirements 1.6, 4.1, 4.3**

- [x] 5. Rewrite `_tts_worker` to use concurrent synthesis tasks
  - Replace the sequential `await self._synthesize_and_enqueue(sentence)` calls with `asyncio.create_task()`.
  - Maintain `pending_tasks: list[asyncio.Task]` in submission order and a `sentence_index: int` counter (reset to 0 at the start of each turn, i.e., after `_END_OF_TOKENS` is processed).
  - Enforce a concurrency cap of 2: when `len(pending_tasks) >= 2`, await and pop `pending_tasks[0]` before creating a new task.
  - After awaiting each task (in order), if the result is not `None` and `interrupt` is not set: enqueue `wav_bytes` to `audio_out_queue`. Log `tts_enqueue_ms` via `pipeline_event("TTS", "first_wav_enqueued", tts_enqueue_ms=tts_enqueue_ms)` when `sentence_index == 0`.
  - On `_END_OF_TOKENS`: call `self._tts.flush()`, create a task for the remaining text (if any), then await all `pending_tasks` in order, enqueuing results. Reset `pending_tasks` and `sentence_index`. Set `self._tts_turn_complete = True`.
  - On interrupt: cancel all tasks in `pending_tasks`, clear the list, reset `sentence_index`, call `_handle_interrupt()`, drain token queue if needed.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.2_

  - [ ]* 5.1 Write property test for audio ordering preservation
    - Mock `_synthesize_and_enqueue` to complete in reverse sentence order (last sentence finishes first).
    - For any turn with N sentences (N ≥ 2), verify `audio_out_queue` receives WAV chunks in sentence order 0, 1, …, N-1.
    - **Property 5: Audio ordering preserved under concurrent synthesis**
    - **Validates: Requirements 2.5**

  - [ ]* 5.2 Write property test for concurrency cap
    - Mock `_synthesize_and_enqueue` with a delay; instrument to count simultaneous in-flight tasks.
    - For any turn with 3+ sentences, verify the peak concurrent task count never exceeds 2.
    - **Property 6: Concurrency cap respected**
    - **Validates: Requirements 2.2**

  - [ ]* 5.3 Write unit test for interrupt cancellation
    - Set `interrupt = True` mid-turn; verify all pending tasks are cancelled and `audio_out_queue` remains empty.
    - **Property 7: Interrupt cancels all pending synthesis**
    - **Validates: Requirements 2.3, 2.4**

- [x] 6. Checkpoint — verify TTS worker behaviour end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Rewrite `_llm_worker` Call 2 to stream tokens directly to `token_queue`
  - Extract the inner `_run_call2()` function and replace it with an inline async helper (or a private method `_stream_call2_to_queue`) that:
    1. Iterates `self._llm.stream(messages, max_tokens=200, temperature=0.65)`.
    2. On each token: checks `self._state.interrupt` (break if set, logging `stream_interrupted`); logs `call2_first_token` with `ttft_ms` on the first token; pushes the token to `token_queue`; appends to `parts`.
    3. Catches stream exceptions and logs `call2_stream_error`.
    4. Returns the assembled `"".join(parts)` string.
  - Retry logic: if `full_response.strip() == ""`, call the helper again (pushing retry tokens to `token_queue` too).
  - Fallback: if still empty, push `_FALLBACK_MESSAGE` as a single token to `token_queue`.
  - Clarification suffix: if `route_result.clarification_suffix`, push `" " + suffix` as an additional token to `token_queue` and append to `full_response`.
  - Push `_END_OF_TOKENS` to `token_queue` after all tokens and suffix.
  - After pushing `_END_OF_TOKENS`: apply `PostProcessor.clean(full_response, detected_language)` → `cleaned_response`; log `turn_complete` with `response_preview`; broadcast `{"type": "llm_text_chunk", "text": cleaned_response}`.
  - History storage: truncate `full_response` to 120 words before storing (unchanged logic).
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [ ]* 7.1 Write property test for token ordering
    - Mock `self._llm.stream` to yield N random tokens.
    - Verify all N tokens appear in `token_queue` in the same order, followed by `_END_OF_TOKENS`.
    - **Property 1: Token ordering preserved end-to-end**
    - **Validates: Requirements 1.1, 1.3**

  - [ ]* 7.2 Write property test for PostProcessor not applied to token_queue
    - Mock `PostProcessor.clean` to return a sentinel string.
    - Verify the sentinel string does NOT appear in `token_queue` (raw tokens do).
    - Verify the sentinel string DOES appear in the `llm_text_chunk` broadcast.
    - **Property 3: PostProcessor.clean does not affect token_queue contents**
    - **Validates: Requirements 1.6**

  - [ ]* 7.3 Write unit tests for retry and fallback paths
    - Mock stream to return empty twice → verify `_FALLBACK_MESSAGE` is pushed to `token_queue`.
    - Mock stream to return empty then non-empty → verify retry tokens appear in `token_queue`.
    - **Property 2: Retry tokens reach token_queue**
    - **Validates: Requirements 1.4, 1.5**

  - [ ]* 7.4 Write unit test for clarification suffix
    - Set `route_result.clarification_suffix = "some suffix"`.
    - Verify `" some suffix"` appears in `token_queue` before `_END_OF_TOKENS`.
    - **Validates: Requirements 1.7**

  - [ ]* 7.5 Write unit test for interrupt mid-stream
    - Set `interrupt = True` after the 3rd token in a 10-token stream.
    - Verify `_END_OF_TOKENS` is pushed and no tokens after the interrupt appear.
    - **Validates: Requirements 1.2**

- [x] 8. Final checkpoint — full pipeline integration
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
- Each task references specific requirements for traceability.
- Changes 1–3 (tasks 1–2) are independent of the pipeline changes and can be verified in isolation.
- Change 4 (task 4) must be completed before Change 3 (task 5) because `_tts_worker` calls `_synthesize_and_enqueue`.
- Change 2 (task 7) is the highest-impact change for TTFA and should be verified with a live session after implementation.
- The `_END_OF_TOKENS` sentinel handling in `_tts_worker` (flush + await all pending tasks) is unchanged in its logical role; only the concurrency mechanism around it changes.
