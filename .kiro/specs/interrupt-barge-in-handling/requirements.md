# Requirements Document

## Introduction

This feature adds robust, production-grade interrupt and barge-in handling to the existing async voice-to-voice chatbot pipeline. The pipeline already works end-to-end (ASR/STT → LLM → TTS → audio output), but interrupt handling is either missing or unreliable. The goal is to introduce a clean, isolated interrupt/barge-in system — using cancellable task wrappers, a turn/session manager, generation IDs, cancellation tokens, and explicit lifecycle cleanup hooks — without redesigning the core five-worker asyncio architecture.

The system must handle the full range of interrupt scenarios: user speech during TTS playback (barge-in), user speech during LLM generation, new user input arriving before the previous assistant response finishes, and stale pipeline state leaking into the next turn. It must also handle rapid consecutive interrupts, delayed streaming chunks arriving after cancellation, and partial TTS/LLM streams.

---

## Glossary

- **Pipeline**: The five-worker asyncio system (`audio_input_worker`, `stt_worker`, `llm_worker`, `tts_worker`, `audio_output_worker`) that processes one WebSocket session.
- **Turn**: A single user-utterance → assistant-response cycle. Each turn has a unique Turn ID.
- **Turn ID**: A monotonically incrementing integer (or UUID) assigned to each new user utterance that enters the pipeline. Used to detect and discard stale work.
- **Interrupt**: Any event that signals the pipeline to abandon the current turn's in-progress generation and return to the listening state. Sources: barge-in from VAD, explicit `{"type": "interrupt"}` WebSocket message.
- **Barge-in**: A specific interrupt triggered by the VAD detecting user speech while the client is playing back TTS audio.
- **InterruptController**: A new server-side component that owns the current Turn ID, exposes a cancellation event, and coordinates cleanup across all pipeline workers.
- **CancellationToken**: An `asyncio.Event` (or equivalent) that is set when an interrupt is issued for the current turn. Workers poll or `await` this token to detect cancellation.
- **Synthesis Task**: An `asyncio.Task` created by `tts_worker` to synthesise a single sentence via Kokoro TTS.
- **Audio Output Queue**: The `audio_out_queue` in `PipelineState` — holds synthesised WAV chunks waiting to be sent to the client.
- **Token Queue**: The `token_queue` in `PipelineState` — holds LLM tokens streamed from `llm_worker` to `tts_worker`.
- **Stale Chunk**: A WAV chunk or LLM token produced for a previous turn that arrives after an interrupt has been issued.
- **Idle/Listening State**: The stable pipeline state where no generation is in progress and the system is ready for new user input.
- **AudioPlayback**: The client-side component (`client/audio_playback.py`) that dequeues and plays WAV chunks through the speaker.
- **VAD**: Voice Activity Detection — the `SileroVAD` component in `client/vad.py` that detects speech start/end and barge-in events.
- **WSClient**: The client-side WebSocket client (`client/ws_client.py`) that sends audio and interrupt messages to the server.

---

## Requirements

### Requirement 1: Turn ID and Interrupt Controller

**User Story:** As a developer, I want each pipeline turn to carry a unique Turn ID managed by a central InterruptController, so that stale work from previous turns can be detected and discarded without scattered boolean flags.

#### Acceptance Criteria

1. THE `InterruptController` SHALL maintain a current Turn ID that is incremented atomically each time a new user utterance begins processing.
2. THE `InterruptController` SHALL expose a `CancellationToken` (an `asyncio.Event`) that is set when an interrupt is issued and cleared when a new turn begins.
3. WHEN a new turn begins, THE `InterruptController` SHALL clear the `CancellationToken` and assign a new Turn ID before any worker begins processing the new utterance.
4. WHEN an interrupt is issued, THE `InterruptController` SHALL set the `CancellationToken` so that all workers polling it detect cancellation within one polling cycle.
5. THE `InterruptController` SHALL be instantiated once per `VoicePipeline` session and stored on `PipelineState` or as a `VoicePipeline` attribute.
6. FOR ALL pipeline workers, THE worker SHALL check the `CancellationToken` or compare the active Turn ID before performing any state-mutating operation (enqueuing audio, updating history, broadcasting to UI).
7. THE `InterruptController` SHALL provide a thread-safe `request_interrupt()` method that can be called from any asyncio coroutine without causing deadlocks.

---

### Requirement 2: Interrupt Signal Reception

**User Story:** As a user, I want my speech to immediately signal an interrupt to the server regardless of which pipeline stage is currently active, so that the system always responds to my barge-in.

#### Acceptance Criteria

1. WHEN the `audio_input_worker` receives a JSON message with `{"type": "interrupt"}` (via binary or text WebSocket frame), THE `audio_input_worker` SHALL call `InterruptController.request_interrupt()` immediately without waiting for the current queue item to be processed.
2. WHEN the `audio_input_worker` receives a new binary PCM16 audio frame while the pipeline state is `"thinking"` or `"speaking"`, THE `audio_input_worker` SHALL call `InterruptController.request_interrupt()` before enqueuing the audio frame.
3. THE `InterruptController.request_interrupt()` method SHALL be idempotent — calling it multiple times for the same turn SHALL have the same effect as calling it once.
4. WHEN the client-side `SileroVAD` detects a barge-in, THE `WSClient` SHALL send `{"type": "interrupt"}` to the server before sending the new PCM16 audio frame.
5. WHEN the client-side `AudioPlayback` receives a `stop()` call during barge-in, THE `AudioPlayback` SHALL immediately halt the currently playing WAV chunk and clear all queued WAV chunks.

---

### Requirement 3: LLM Stream Cancellation

**User Story:** As a developer, I want the LLM token stream to stop producing tokens as soon as an interrupt is issued, so that no stale tokens reach the TTS worker after cancellation.

#### Acceptance Criteria

1. WHILE the `llm_worker` is streaming tokens from the LLM API, THE `llm_worker` SHALL check the `CancellationToken` on every token received and stop consuming the stream immediately when the token is set.
2. WHEN the `llm_worker` detects cancellation mid-stream, THE `llm_worker` SHALL push the `_END_OF_TOKENS` sentinel to `token_queue` to unblock the `tts_worker` before exiting the current turn's processing loop.
3. WHEN the `llm_worker` detects cancellation, THE `llm_worker` SHALL NOT append any partial response to the conversation history.
4. WHEN the `llm_worker` detects cancellation, THE `llm_worker` SHALL NOT broadcast any `llm_text_chunk` message to the BrowserUI for the cancelled turn.
5. IF a delayed LLM token arrives after the `CancellationToken` is set, THEN THE `llm_worker` SHALL discard the token without enqueuing it.
6. WHEN the `llm_worker` finishes draining a cancelled stream, THE `llm_worker` SHALL log the cancellation event at INFO level with the Turn ID and the number of tokens discarded, even when zero tokens were discarded.

---

### Requirement 4: TTS Synthesis Task Cancellation

**User Story:** As a developer, I want all in-flight TTS synthesis tasks to be cancelled immediately on interrupt, so that no stale WAV chunks are produced or enqueued after cancellation.

#### Acceptance Criteria

1. WHEN the `tts_worker` detects that the `CancellationToken` is set, THE `tts_worker` SHALL cancel all pending `asyncio.Task` synthesis tasks in its `pending_tasks` list.
2. WHEN a synthesis task is cancelled, THE synthesis task SHALL NOT enqueue its WAV result to `audio_out_queue`.
3. THE `tts_worker` SHALL check the `CancellationToken` before enqueuing each synthesised WAV chunk, even if the synthesis task completed before the interrupt was detected.
4. WHEN the `tts_worker` detects cancellation, THE `tts_worker` SHALL drain the `token_queue` until the `_END_OF_TOKENS` sentinel is received, discarding all tokens.
5. WHEN the `tts_worker` detects cancellation, THE `tts_worker` SHALL call `TTSRouter.flush()` to reset the sentence accumulation buffer.
6. AFTER cancellation cleanup, THE `tts_worker` SHALL reset its `pending_tasks` list to empty and its `sentence_index` to zero before processing the next turn.
7. IF a synthesis task completes after the `CancellationToken` is set, THEN THE `tts_worker` SHALL discard the result without enqueuing it to `audio_out_queue`.

---

### Requirement 5: Audio Output Queue Flush

**User Story:** As a user, I want all queued audio to stop playing immediately when I interrupt, so that I never hear stale assistant speech after my barge-in.

#### Acceptance Criteria

1. WHEN the `audio_output_worker` detects that the `CancellationToken` is set, THE `audio_output_worker` SHALL drain all items from `audio_out_queue` without sending them to the client.
2. WHEN the `audio_output_worker` completes the interrupt flush, THE `audio_output_worker` SHALL transition the pipeline state to `"listening"` and clear the `CancellationToken` via `InterruptController`.
3. THE `audio_output_worker` SHALL check the `CancellationToken` before sending each WAV chunk, even if the chunk was dequeued before the interrupt was detected.
4. WHEN the `audio_output_worker` flushes the queue on interrupt, THE `audio_output_worker` SHALL log the number of discarded chunks and the Turn ID.
5. AFTER the interrupt flush, THE `audio_output_worker` SHALL reset its per-turn counters (`first_chunk`, `chunk_send_time`, `_turn_audio_duration_ms`, `_tts_turn_complete`) to their initial values.
6. THE `audio_output_worker` SHALL NOT send a `"speaking"` status message for a turn whose `CancellationToken` was set before the first chunk was sent.

---

### Requirement 6: Stable Return to Listening State

**User Story:** As a user, I want the pipeline to return to a clean, stable listening state after every interrupt, so that my next utterance is processed correctly without residual state from the interrupted turn.

#### Acceptance Criteria

1. WHEN interrupt cleanup is complete across all workers, THE `InterruptController` SHALL transition the pipeline state to `"listening"` exactly once per interrupt event. IF the state transition fails, THEN THE `InterruptController` SHALL retry the transition up to 3 times with a 50ms delay between attempts before logging an ERROR and leaving the pipeline in a safe idle state.
2. THE `InterruptController` SHALL ensure that the `token_queue`, `audio_out_queue`, and `transcript_queue` are all empty before broadcasting the `"listening"` status to clients.
3. WHEN the pipeline returns to `"listening"` after an interrupt, THE `PipelineState.interrupt` flag SHALL be `False` and the `CancellationToken` SHALL be cleared.
4. WHEN the pipeline returns to `"listening"` after an interrupt, THE `tts_worker`'s sentence accumulation buffer SHALL be actively cleared during interrupt cleanup by calling `TTSRouter.flush()` before the `"listening"` state is broadcast.
5. WHEN the pipeline returns to `"listening"` after an interrupt, THE `llm_worker` SHALL be ready to process the next item from `transcript_queue` without any residual state from the interrupted turn.
6. IF a new user utterance arrives in `transcript_queue` while interrupt cleanup is still in progress, THEN THE `llm_worker` SHALL wait for the `CancellationToken` to be cleared before processing the new utterance.

---

### Requirement 7: Rapid Consecutive Interrupt Safety

**User Story:** As a developer, I want the system to handle rapid consecutive interrupts without deadlocks, orphaned tasks, or corrupted state, so that aggressive barge-in behaviour does not destabilise the pipeline.

#### Acceptance Criteria

1. WHEN multiple interrupt signals arrive within 100ms of each other, THE `InterruptController` SHALL process them as a single interrupt event for the current turn (idempotent behaviour).
2. WHEN a new user utterance begins processing while a previous interrupt cleanup is still in progress, THE `InterruptController` SHALL complete the previous cleanup before starting the new turn.
3. THE pipeline SHALL NOT create new synthesis tasks for a turn whose `CancellationToken` is already set.
4. THE pipeline SHALL NOT have more than one active `InterruptController.request_interrupt()` call executing concurrently for the same turn.
5. WHEN the `audio_input_worker` receives a new PCM16 frame immediately after sending an interrupt, THE `audio_input_worker` SHALL enqueue the new frame only after the interrupt has been acknowledged (i.e. the `CancellationToken` has been set).
6. FOR ALL rapid consecutive interrupts, THE pipeline SHALL return to `"listening"` state within 500ms of the last interrupt signal being received, measured from the time the interrupt is set on the `CancellationToken`.

---

### Requirement 8: Stale Chunk and Delayed Callback Safety

**User Story:** As a developer, I want delayed streaming chunks and async callbacks that arrive after cancellation to be silently discarded, so that stale data never corrupts the next turn's state.

#### Acceptance Criteria

1. WHEN a synthesised WAV chunk is produced by a synthesis task that was cancelled, THE `tts_worker` SHALL discard the chunk by comparing the synthesis task's Turn ID against the `InterruptController`'s current Turn ID.
2. WHEN an LLM streaming callback delivers a token after the `CancellationToken` is set, THE `llm_worker` SHALL discard the token without side effects.
3. WHEN a `transcript_queue` item is dequeued by `llm_worker` and the Turn ID on the item does not match the `InterruptController`'s current Turn ID, THE `llm_worker` SHALL discard the item and log a warning.
4. THE `_await_and_enqueue` helper in `tts_worker` SHALL check the `CancellationToken` after awaiting each synthesis task and discard the result if the token is set.
5. IF the `audio_output_worker` dequeues a WAV chunk and the `CancellationToken` is set, THEN THE `audio_output_worker` SHALL discard the chunk without sending it.

---

### Requirement 9: Resource Release and Cleanup Hooks

**User Story:** As a developer, I want explicit lifecycle cleanup hooks to be called on every interrupt, so that no async tasks, buffers, or temporary state are orphaned after cancellation.

#### Acceptance Criteria

1. THE `InterruptController` SHALL expose a `cleanup()` coroutine that cancels all tracked synthesis tasks, drains both `token_queue` and `audio_out_queue`, and resets all per-turn state.
2. WHEN `InterruptController.cleanup()` is called, THE `InterruptController` SHALL await the cancellation of all tracked synthesis tasks with a timeout of 2 seconds before proceeding.
3. IF a synthesis task does not complete within the 2-second cancellation timeout, THEN THE `InterruptController` SHALL log a warning and proceed without waiting further.
4. WHEN `VoicePipeline.stop()` is called (on WebSocket disconnect or server shutdown), THE `VoicePipeline` SHALL call `InterruptController.cleanup()` before cancelling the worker tasks.
5. THE `InterruptController` SHALL track all synthesis tasks created by `tts_worker` so that `cleanup()` can cancel them without the `tts_worker` needing to maintain its own task registry.
6. AFTER `InterruptController.cleanup()` completes, THE `PipelineState` queues (`token_queue`, `audio_out_queue`) SHALL be empty.

---

### Requirement 10: No Deadlocks or Blocking Behaviour

**User Story:** As a developer, I want the interrupt system to be free of deadlocks and blocking calls, so that the asyncio event loop remains responsive during and after interrupts.

#### Acceptance Criteria

1. THE `InterruptController.request_interrupt()` method SHALL be a non-blocking coroutine or synchronous method that sets the `CancellationToken` without awaiting any other coroutine.
2. THE `InterruptController` SHALL NOT use `asyncio.Lock` in any code path that is called from within a worker's main loop, to prevent priority inversion.
3. WHEN cancelling synthesis tasks, THE `tts_worker` SHALL use `task.cancel()` followed by `asyncio.gather(*tasks, return_exceptions=True)` rather than awaiting each task individually, to avoid sequential blocking.
4. THE `audio_output_worker` interrupt flush SHALL complete within one event loop iteration after the `CancellationToken` is set, using `get_nowait()` to drain the queue without blocking.
5. THE `llm_worker` stream cancellation SHALL NOT block the event loop while waiting for the LLM API to acknowledge the cancellation — it SHALL break out of the streaming loop immediately and let the underlying HTTP connection close asynchronously.
6. FOR ALL workers, interrupt detection SHALL occur within at most one `asyncio.wait_for(..., timeout=0.1)` polling cycle (≤ 100ms) after the `CancellationToken` is set.

---

### Requirement 11: Observability and Debuggability

**User Story:** As a developer, I want all interrupt events and cleanup actions to be logged with Turn IDs and timing information, so that I can diagnose interrupt-related issues in production logs.

#### Acceptance Criteria

1. WHEN `InterruptController.request_interrupt()` is called, THE `InterruptController` SHALL log an INFO-level event containing the Turn ID, the interrupt source (barge-in or explicit message), and the current pipeline state.
2. WHEN each worker detects the `CancellationToken` and begins cleanup, THE worker SHALL log a DEBUG-level event containing the worker name, Turn ID, and the number of items discarded.
3. WHEN the pipeline returns to `"listening"` after an interrupt, THE `InterruptController` SHALL log an INFO-level event containing the Turn ID, total cleanup duration in milliseconds, and the new Turn ID.
4. WHEN a stale chunk or token is discarded due to a Turn ID mismatch, THE discarding worker SHALL log a DEBUG-level event with the stale Turn ID and the current Turn ID.
5. THE `InterruptController` SHALL expose a `last_interrupt_duration_ms` property that returns the wall-clock time in milliseconds between the most recent `request_interrupt()` call and the completion of cleanup.

---

### Requirement 12: Client-Side Interrupt Completeness

**User Story:** As a user, I want the client to stop local audio playback immediately on barge-in and send the interrupt signal before the new audio, so that the server and client are always in sync.

#### Acceptance Criteria

1. WHEN `SileroVAD` fires the `on_barge_in` callback, THE client `main.py` SHALL call `AudioPlayback.stop()` synchronously before scheduling `WSClient.send_interrupt()`, ensuring both the actual stop call and the correct ordering are enforced.
2. WHEN `AudioPlayback.stop()` is called, THE `AudioPlayback` SHALL set `_running` to `False`, drain the `asyncio.Queue` of all pending WAV chunks, and stop the currently playing sounddevice stream if one is active.
3. WHEN `WSClient.send_interrupt()` is called, THE `WSClient` SHALL send the interrupt message before any subsequent `send_audio()` call for the new utterance.
4. WHEN the client receives a `{"type": "status", "state": "listening"}` message after an interrupt, THE `SileroVAD` SHALL have its `_client_speaking` flag set to `False` so that barge-in detection is re-armed.
5. THE `AudioPlayback` SHALL expose a `stop_stream()` method that calls `sounddevice.stop()` to halt the currently playing audio immediately, in addition to clearing the queue.
6. WHEN `AudioPlayback.stop()` is called while `_play_wav_blocking` is executing in a thread executor, THE `AudioPlayback` SHALL signal the executor thread to stop playback via `sounddevice.stop()` before the next chunk begins.
