# Implementation Plan: Interrupt / Barge-In Handling

## Overview

Introduce a production-grade interrupt and barge-in system to the existing five-worker asyncio voice pipeline. The implementation adds `InterruptController` to `server/pipeline.py`, wires it into all five workers, and updates the client-side `AudioPlayback`, `SileroVAD`, and `main.py` to stop local playback before sending the interrupt signal. No routing, LLM, STT, or TTS backend files are touched.

## Tasks

- [x] 1. Add `InterruptController` class to `server/pipeline.py`
  - [x] 1.1 Implement `InterruptController` with `cancelled` event, `_turn_id`, `_synthesis_tasks`, and timing fields
    - Define the class in `server/pipeline.py` above `PipelineState`
    - Implement `request_interrupt(source)` — synchronous, idempotent, sets `cancelled` and records `_interrupt_start`
    - Implement `begin_turn()` — clears `cancelled`, increments `_turn_id`, clears `_synthesis_tasks`, computes `_last_duration_ms`
    - Implement `register_task(task)`, `cleanup()` (async, 2s timeout), `wait_cleared()` (async poll), and `last_interrupt_duration_ms` property
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 9.1, 9.2, 9.3, 9.5, 10.1, 10.2, 11.1, 11.3, 11.5_

  - [ ]* 1.2 Write property test for Turn ID monotonicity (Property 1)
    - **Property 1: Turn ID monotonicity**
    - For any N ∈ [1, 50], N calls to `begin_turn()` produce strictly increasing IDs
    - **Validates: Requirements 1.1**

  - [ ]* 1.3 Write property test for CancellationToken round-trip (Property 2)
    - **Property 2: CancellationToken round-trip**
    - For any sequence of `request_interrupt()` / `begin_turn()` calls, `cancelled` state matches expected
    - **Validates: Requirements 1.2, 1.3, 6.3**

  - [ ]* 1.4 Write property test for `request_interrupt()` idempotency (Property 3)
    - **Property 3: `request_interrupt()` idempotency**
    - For any N ≥ 1 calls to `request_interrupt()`, observable state equals state after a single call
    - **Validates: Requirements 2.3, 7.1**

  - [ ]* 1.5 Write unit tests for `InterruptController` in isolation
    - Test `cleanup()` cancels all registered tasks within 2s timeout
    - Test `cleanup()` logs a warning and proceeds when tasks exceed the 2s timeout
    - Test `last_interrupt_duration_ms` returns a positive value after a complete interrupt cycle
    - Test `wait_cleared()` returns promptly after `begin_turn()` is called
    - _Requirements: 9.1, 9.2, 9.3, 11.5_

- [x] 2. Update `PipelineState` and `VoicePipeline.__init__` / `stop()`
  - [x] 2.1 Add `interrupt_controller` field to `PipelineState` and wire `InterruptController` in `VoicePipeline`
    - Add `interrupt_controller: "InterruptController | None" = None` to the `PipelineState` dataclass
    - In `VoicePipeline.__init__`, instantiate `self._ic = InterruptController(state.session_id)` and assign `state.interrupt_controller = self._ic`
    - Promote `VoicePipeline.stop()` to `async`; call `await self._ic.cleanup()` before cancelling worker tasks; update the `finally` block in `_audio_input_worker` to use `asyncio.ensure_future(self.stop())`
    - _Requirements: 1.5, 9.4_

  - [ ]* 2.2 Write property test for cleanup task cancellation with timeout (Property 8)
    - **Property 8: Cleanup task cancellation with timeout**
    - For any set of synthesis tasks registered with `InterruptController`, `cleanup()` cancels all tasks and completes within 2s + ε even if tasks do not respond to cancellation
    - **Validates: Requirements 9.1, 9.2, 9.3**

- [x] 3. Update `_audio_input_worker` to call `InterruptController.request_interrupt()`
  - [x] 3.1 Replace `self._state.interrupt = True` assignments with `InterruptController` calls in `_audio_input_worker`
    - On receiving `{"type": "interrupt"}` (binary or text frame): call `self._ic.request_interrupt(source="explicit_message")` and keep `self._state.interrupt = True` in sync
    - On receiving a new PCM16 frame while `self._state.state` is `"thinking"` or `"speaking"`: call `self._ic.request_interrupt(source="new_audio_frame")` before enqueuing the frame
    - _Requirements: 2.1, 2.2, 2.3, 10.1, 11.1_

- [x] 4. Update `_llm_worker` to use `InterruptController`
  - [x] 4.1 Add `begin_turn()` call at turn start and replace interrupt checks with `IC.cancelled.is_set()` in `_llm_worker`
    - At the top of the per-turn block (after dequeuing from `transcript_queue`), call `turn_id = self._ic.begin_turn()`
    - Add a guard at the top of the outer `while True` loop: if `cancelled.is_set()`, await `self._ic.wait_cleared()` with a 500ms timeout before processing the next transcript
    - Replace `if self._state.interrupt:` in the token-stream loop with `if self._ic.cancelled.is_set():`; on cancellation, push `_END_OF_TOKENS`, log at INFO with `turn_id` and tokens discarded, and `continue` (skip history append and UI broadcast)
    - _Requirements: 1.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 6.5, 6.6, 7.2, 8.2, 8.3, 10.5, 11.2_

  - [ ]* 4.2 Write property test for LLM stream cancellation completeness (Property 5)
    - **Property 5: LLM stream cancellation completeness**
    - For any token stream of length N and interrupt position K (0 ≤ K ≤ N), `token_queue` receives exactly the tokens before position K followed by exactly one `_END_OF_TOKENS`, and no tokens at positions K…N are enqueued
    - **Validates: Requirements 3.1, 3.2, 3.5**

  - [ ]* 4.3 Write property test for no state mutation after cancellation (Property 4)
    - **Property 4: No state mutation after cancellation**
    - For any worker iteration where `cancelled.is_set()` is `True`, no item is enqueued to `token_queue`, `audio_out_queue`, or `transcript_queue`, and conversation history is not modified
    - **Validates: Requirements 1.6, 3.3, 3.4, 4.2, 4.3, 5.3, 8.1, 8.2, 8.3, 8.4, 8.5**

- [x] 5. Update `_tts_worker` to use `InterruptController`
  - [x] 5.1 Register synthesis tasks with `InterruptController` and replace interrupt checks in `_tts_worker`
    - After each `asyncio.create_task(self._synthesize_and_enqueue(...))`, call `self._ic.register_task(task)`
    - Replace `if self._state.interrupt:` with `if self._ic.cancelled.is_set():` in the token-processing loop; on cancellation, cancel all `pending_tasks` via `asyncio.gather(*pending_tasks, return_exceptions=True)`, clear `pending_tasks`, reset `sentence_index = 0`, call `self._tts.flush()`, and drain `token_queue` until `_END_OF_TOKENS`
    - In `_await_and_enqueue`, add a `cancelled.is_set()` check after awaiting the task; discard the result with a DEBUG log if the token is set
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 7.3, 8.1, 8.4, 9.5, 10.3, 11.2_

  - [ ]* 5.2 Write property test for TTS task cancellation completeness (Property 6)
    - **Property 6: TTS task cancellation completeness**
    - For any M pending synthesis tasks registered with `InterruptController`, after `cancelled` is set and `tts_worker` performs cleanup, all M tasks are in a cancelled or done state and `audio_out_queue` is empty
    - **Validates: Requirements 4.1, 4.2, 4.6, 4.7**

- [x] 6. Update `_audio_output_worker` to use `InterruptController`
  - [x] 6.1 Replace interrupt checks with `IC.cancelled.is_set()` and call `begin_turn()` after flush in `_audio_output_worker`
    - Replace the existing `self._state.interrupt` check at the top of the loop with `if self._ic.cancelled.is_set():`
    - In the flush block: drain `audio_out_queue` via `get_nowait()` loop, log discarded chunk count and `turn_id`, reset per-turn counters (`first_chunk`, `_turn_audio_duration_ms`, `_tts_turn_complete`), set `self._state.interrupt = False`, call `await self._set_state("listening")` with up to 3 retries (50ms delay), then call `self._ic.begin_turn()`
    - Add a per-chunk guard: `if self._ic.cancelled.is_set(): continue` before sending each WAV chunk
    - Add a guard before sending `"speaking"` status: skip if `cancelled.is_set()` when `first_chunk` is `True`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 7.6, 8.5, 10.4, 11.2, 11.3_

  - [ ]* 6.2 Write property test for audio output queue flush completeness (Property 7)
    - **Property 7: Audio output queue flush completeness**
    - For any N WAV chunks in `audio_out_queue` when `audio_output_worker` detects `cancelled.is_set()`, after the flush loop completes, `audio_out_queue` is empty and exactly N chunks were discarded
    - **Validates: Requirements 5.1, 5.4**

- [ ] 7. Checkpoint — server-side interrupt system complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Update `client/audio_playback.py` — `stop()` and `stop_stream()`
  - [x] 8.1 Add `sounddevice.stop()` to `AudioPlayback.stop()` and add `stop_stream()` method
    - In `stop()`, after draining the queue, call `self.stop_stream()`
    - Add `stop_stream()` method that calls `sounddevice.stop()` wrapped in `try/except Exception` with a DEBUG log on failure
    - _Requirements: 2.5, 12.2, 12.5, 12.6_

  - [ ]* 8.2 Write unit tests for `AudioPlayback` stop behaviour
    - Test `stop()` drains the queue, sets `_running = False`, and calls `sounddevice.stop()` (mocked)
    - Test `stop_stream()` handles `sounddevice` exceptions gracefully without raising
    - _Requirements: 12.2, 12.5, 12.6_

- [x] 9. Update `client/main.py` — `_on_barge_in()` and `_on_status()`
  - [x] 9.1 Update `_on_barge_in()` to call `audio_playback.stop()` before `ws_client.send_interrupt()` and update `_on_status()` to re-arm VAD
    - In `_on_barge_in()`, call `audio_playback.stop()` synchronously first, then schedule `ws_client.send_interrupt()` via `loop.call_soon_threadsafe`
    - In `_on_status()`, add `if state == "listening": vad.set_speaking(False)` to explicitly re-arm barge-in detection
    - _Requirements: 12.1, 12.3, 12.4_

  - [ ]* 9.2 Write unit tests for client-side barge-in ordering and VAD re-arm
    - Test `_on_barge_in` calls `stop()` before `send_interrupt()` (ordering verified via mock call sequence)
    - Test `_on_status("listening")` sets `vad._client_speaking` to `False`
    - _Requirements: 12.1, 12.4_

- [ ] 10. Integration tests for full interrupt lifecycle
  - [ ]* 10.1 Write integration test: interrupt during LLM streaming
    - Use real asyncio event loop with mock LLM/TTS backends and in-memory queues
    - Verify `_END_OF_TOKENS` is pushed, history is not updated, and UI broadcast is skipped
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 10.2 Write integration test: interrupt during TTS synthesis
    - Verify pending synthesis tasks are cancelled, `audio_out_queue` is empty after flush
    - _Requirements: 4.1, 4.2, 5.1_

  - [ ]* 10.3 Write integration test: rapid consecutive interrupts
    - Fire 5 interrupt signals within 100ms; verify pipeline returns to `"listening"` within 500ms
    - _Requirements: 7.1, 7.6_

  - [ ]* 10.4 Write integration test: stale WAV chunk arriving after `begin_turn()`
    - Verify the chunk is discarded without being enqueued to `audio_out_queue`
    - _Requirements: 8.1, 8.4_

  - [ ]* 10.5 Write integration test: full barge-in lifecycle
    - barge-in → interrupt → cleanup → `"listening"` → new turn processes correctly
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [~] 11. Final checkpoint — all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- The existing `interrupt: bool` field on `PipelineState` is kept in sync with `IC.cancelled` throughout — do not remove it
- `VoicePipeline.stop()` is promoted to `async`; the `finally` block in `_audio_input_worker` must use `asyncio.ensure_future(self.stop())` to avoid blocking
- Property tests use [Hypothesis](https://hypothesis.readthedocs.io/) with `max_examples = 100`; place them in `server/test_interrupt_controller.py`
- Client-side tests go in `client/test_audio_playback.py` and `client/test_main.py`
- Integration tests go in `server/test_interrupt_integration.py`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1"] },
    { "id": 3, "tasks": ["4.1", "8.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "5.1", "8.2", "9.1"] },
    { "id": 5, "tasks": ["5.2", "6.1", "9.2"] },
    { "id": 6, "tasks": ["6.2", "10.1", "10.2", "10.3", "10.4", "10.5"] }
  ]
}
```
