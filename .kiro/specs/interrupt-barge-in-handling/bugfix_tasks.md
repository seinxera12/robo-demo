# Bugfix Implementation Tasks

## Tasks

- [x] 1. Fix spurious interrupt in `_audio_input_worker`
  - [x] 1.1 Add `robo_active` gate to PCM16-triggered interrupt condition
    - In `_audio_input_worker` (~line 340 of `server/pipeline.py`), change the condition:
      ```python
      # BEFORE
      if self._state.state in ("thinking", "speaking"):
      
      # AFTER
      if self._state.state in ("thinking", "speaking") and self._state.robo_active:
      ```
    - Do NOT change the explicit `{"type": "interrupt"}` binary-message handling block above
      this condition — it remains ungated by `robo_active`
    - Do NOT change the text-path `{"type": "interrupt"}` handling in the `elif raw_text` branch
    - Do NOT change the `await self._state.audio_queue.put(raw_bytes)` line — frames are always
      enqueued regardless of whether an interrupt is triggered
    - _Requirements: bugfix.md 2.1, 2.2, 2.3, 2.4, 3.1, 3.2_

  - [ ]* 1.2 Write property test for spurious interrupt fix
    - Property: for any `(state ∈ {"thinking","speaking"}, robo_active=False, n_frames ∈ [1..50])`,
      after processing n_frames PCM16 frames, `request_interrupt` call count = 0
    - Property: for any `(state ∈ {"thinking","speaking"}, robo_active=True, n_frames ∈ [1..50])`,
      after processing n_frames PCM16 frames, `request_interrupt` call count ≥ 1
    - Also cover: explicit `{"type":"interrupt"}` message with `robo_active=False` → interrupt
      fires (ungated path preserved)
    - _Requirements: bugfix.md 2.1, 2.2, 2.3, 2.4_

- [x] 2. Fix LLM stream cancellation in `_llm_worker`
  - [x] 2.1 Remove mid-stream `cancelled.is_set()` check from `_stream_call2_to_queue`
    - Inside `_stream_call2_to_queue` (nested function inside `_llm_worker`), remove the entire
      block:
      ```python
      if self._ic.cancelled.is_set():
          pipeline_event("LLM", "stream_interrupted",
                         session=sid, tokens_so_far=token_count)
          break
      ```
    - This block appears at the top of the `async for token in self._llm.stream(...)` loop body,
      before the `first_token_logged` check
    - Keep the post-stream check that appears AFTER `_stream_call2_to_queue` returns — this block
      is correct and must not be removed:
      ```python
      if self._ic.cancelled.is_set():
          token_count = len(full_response.split()) if full_response else 0
          await self._state.token_queue.put(_END_OF_TOKENS)
          logger.info("LLM cancelled turn_id=%d tokens_discarded=%d session=%s", ...)
          continue
      ```
    - _Requirements: bugfix.md 2.5, 2.6, 3.7_

  - [ ]* 2.2 Write property test for LLM stream preservation
    - Property: for any token stream of length N (1..100) and cancellation position K (0..N),
      all N tokens are enqueued to `token_queue` after the fixed `_stream_call2_to_queue` returns
      (not just K tokens)
    - Property: when `cancelled` is set after the stream ends (post-stream check), exactly one
      `_END_OF_TOKENS` is pushed and history/broadcast is skipped
    - _Requirements: bugfix.md 2.5, 3.7_

- [ ] 3. Verify end-to-end barge-in flow
  - Manually verify: with `robo_active=False`, ambient audio during TTS playback (`state="speaking"`)
    does NOT trigger an interrupt — TTS continues uninterrupted
  - Manually verify: with `robo_active=False`, ambient audio during LLM generation
    (`state="thinking"`) does NOT trigger an interrupt — LLM continues uninterrupted
  - Manually verify: with `robo_active=True`, speaking during TTS playback triggers interrupt,
    stops TTS audio output, but LLM continues streaming tokens (which are drained and discarded
    by `_tts_worker` via `_drain_token_queue()`)
  - Manually verify: explicit `{"type": "interrupt"}` WebSocket message still triggers interrupt
    regardless of `robo_active` value
  - Manually verify: after a barge-in, the pipeline correctly transitions back to `"listening"`
    and accepts the next user turn without getting stuck
