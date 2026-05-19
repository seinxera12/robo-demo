# Design Document — Interrupt / Barge-In Handling

## Overview

This design adds a production-grade interrupt and barge-in system to the existing five-worker asyncio voice pipeline. The core pipeline architecture (`audio_input_worker → stt_worker → llm_worker → tts_worker → audio_output_worker`) is preserved unchanged. The interrupt system is introduced as a thin, isolated layer that sits alongside the pipeline and coordinates cancellation without touching the routing or data-flow logic.

The central addition is `InterruptController` — a new class in `server/pipeline.py` that owns the current Turn ID, exposes a `CancellationToken` (`asyncio.Event`), tracks in-flight synthesis tasks, and provides the single `request_interrupt()` entry point. Workers are updated to check the token at their natural polling boundaries. The client side receives matching updates to `AudioPlayback`, `SileroVAD`, and `main.py` to ensure local playback stops before the interrupt signal is sent.

---

## Architecture

### Component Map

```
┌─────────────────────────────────────────────────────────────────────┐
│  VoicePipeline (server/pipeline.py)                                 │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  InterruptController  (NEW)                                  │   │
│  │  ─ current_turn_id: int                                      │   │
│  │  ─ cancelled: asyncio.Event  (CancellationToken)             │   │
│  │  ─ _synthesis_tasks: list[asyncio.Task]                      │   │
│  │  ─ _interrupt_start: float                                   │   │
│  │  + request_interrupt(source)  → synchronous, non-blocking    │   │
│  │  + begin_turn()               → clears token, bumps turn_id  │   │
│  │  + register_task(task)        → tracks synthesis tasks       │   │
│  │  + cleanup()                  → async, cancels all tasks     │   │
│  │  + last_interrupt_duration_ms → property                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  PipelineState (MODIFIED)                                           │
│  ─ interrupt: bool  (kept for backward compat, driven by IC)        │
│  ─ interrupt_controller: InterruptController  (NEW field)           │
│                                                                     │
│  Workers (MODIFIED — interrupt checks only)                         │
│  ─ audio_input_worker  → calls IC.request_interrupt()               │
│  ─ llm_worker          → checks IC.cancelled.is_set() per token     │
│  ─ tts_worker          → checks IC.cancelled.is_set(), calls        │
│                           IC.register_task() for each synthesis task │
│  ─ audio_output_worker → checks IC.cancelled.is_set() per chunk,    │
│                           calls IC.begin_turn() after flush          │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Client                                                             │
│                                                                     │
│  AudioPlayback (MODIFIED)                                           │
│  ─ stop()         → sets _running=False, drains queue,              │
│                     calls sounddevice.stop()                        │
│  ─ stop_stream()  → calls sounddevice.stop() directly               │
│                                                                     │
│  SileroVAD (MODIFIED)                                               │
│  ─ set_speaking() → also resets _client_speaking=False on           │
│                     "listening" status                              │
│                                                                     │
│  main.py (MODIFIED)                                                 │
│  ─ _on_barge_in() → calls audio_playback.stop() first,             │
│                     then schedules ws_client.send_interrupt()       │
│  ─ _on_status()   → calls vad.set_speaking(False) on "listening"   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## New Component: `InterruptController`

### Placement

`InterruptController` is defined in `server/pipeline.py`, instantiated once inside `VoicePipeline.__init__()`, and stored as `self._ic`. A reference is also placed on `PipelineState` as `interrupt_controller` so workers can reach it without going through `VoicePipeline`.

### Class Definition

```python
class InterruptController:
    """Owns the current Turn ID and CancellationToken for one pipeline session.

    All methods are safe to call from any asyncio coroutine on the same
    event loop. No asyncio.Lock is used in any hot-path method.
    """

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id[:8]
        self._turn_id: int = 0
        self.cancelled: asyncio.Event = asyncio.Event()
        self._synthesis_tasks: list[asyncio.Task] = []
        self._interrupt_start: float = 0.0
        self._last_duration_ms: float = 0.0

    # --- Hot-path (synchronous, non-blocking) ---

    def request_interrupt(self, source: str = "unknown") -> None:
        """Set the CancellationToken. Idempotent — safe to call multiple times."""
        if self.cancelled.is_set():
            return  # already interrupted for this turn
        self._interrupt_start = time.monotonic()
        self.cancelled.set()
        logger.info(
            "INTERRUPT turn_id=%d source=%s session=%s",
            self._turn_id, source, self._session_id,
        )

    def begin_turn(self) -> int:
        """Clear the CancellationToken and assign a new Turn ID. Returns new ID."""
        self._turn_id += 1
        self.cancelled.clear()
        self._synthesis_tasks.clear()
        if self._interrupt_start:
            self._last_duration_ms = (time.monotonic() - self._interrupt_start) * 1000
            logger.info(
                "INTERRUPT_COMPLETE turn_id=%d cleanup_ms=%.1f new_turn_id=%d session=%s",
                self._turn_id - 1, self._last_duration_ms,
                self._turn_id, self._session_id,
            )
            self._interrupt_start = 0.0
        return self._turn_id

    @property
    def current_turn_id(self) -> int:
        return self._turn_id

    @property
    def last_interrupt_duration_ms(self) -> float:
        return self._last_duration_ms

    # --- Task registry ---

    def register_task(self, task: asyncio.Task) -> None:
        """Register a synthesis task so cleanup() can cancel it."""
        self._synthesis_tasks.append(task)

    # --- Cleanup (async, called on stop or explicit cleanup) ---

    async def cleanup(self) -> None:
        """Cancel all tracked synthesis tasks and wait up to 2 s for them."""
        tasks = [t for t in self._synthesis_tasks if not t.done()]
        if not tasks:
            return
        for t in tasks:
            t.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "IC.cleanup: %d task(s) did not finish within 2s (session=%s)",
                len(tasks), self._session_id,
            )
        self._synthesis_tasks.clear()
```

### Key Design Decisions

- **No `asyncio.Lock`** in `request_interrupt()` or `begin_turn()`. Both are called from within worker loops. Using a lock would risk priority inversion. The `asyncio.Event` is itself thread-safe for `set()`/`clear()`/`is_set()`.
- **Idempotency** is enforced by the early-return guard in `request_interrupt()`: if `cancelled` is already set, the call is a no-op.
- **`begin_turn()` is the only place** the token is cleared. This ensures the token stays set until `audio_output_worker` has finished flushing and explicitly calls `begin_turn()`.
- **`_synthesis_tasks` is cleared in `begin_turn()`** so stale task references from the previous turn do not accumulate.

---

## Modified: `PipelineState`

Add one field:

```python
@dataclass
class PipelineState:
    ...
    interrupt_controller: "InterruptController | None" = None  # set by VoicePipeline
```

The existing `interrupt: bool` field is kept. `audio_input_worker` continues to set `self._state.interrupt = True` for backward compatibility with the `tts_worker` and `audio_output_worker` checks that already exist. `InterruptController.request_interrupt()` sets both the `asyncio.Event` and `self._state.interrupt` so both mechanisms stay in sync.

---

## Modified: `VoicePipeline`

### `__init__`

```python
self._ic = InterruptController(state.session_id)
state.interrupt_controller = self._ic
```

### `stop()`

```python
async def stop(self) -> None:  # promoted to async
    await self._ic.cleanup()
    for task in self._tasks:
        if not task.done():
            task.cancel()
```

> Note: `stop()` is currently synchronous. It is called from `_audio_input_worker`'s `finally` block. The promotion to `async` requires the `finally` block to use `asyncio.ensure_future(self.stop())` or a helper. See the Tasks section for the exact change.

---

## Modified: `_audio_input_worker`

Replace the two `self._state.interrupt = True` assignments with calls to `InterruptController`:

```python
# Binary interrupt message
if parsed.get("type") == "interrupt":
    self._ic.request_interrupt(source="explicit_message")
    self._state.interrupt = True   # keep flag in sync
    continue

# New PCM16 frame while thinking/speaking
if self._state.state in ("thinking", "speaking"):
    self._ic.request_interrupt(source="new_audio_frame")
    self._state.interrupt = True
```

The second guard (triggering on new PCM16 while thinking/speaking) is a new addition — the current code enqueues the frame unconditionally.

---

## Modified: `_llm_worker`

### Turn-start: call `begin_turn()`

At the top of the per-turn block, after dequeuing from `transcript_queue`:

```python
turn_id = self._ic.begin_turn()
```

This clears the `CancellationToken` and assigns a new Turn ID before any LLM work begins.

### Token-stream loop: check `cancelled` event

Replace `if self._state.interrupt:` with:

```python
if self._ic.cancelled.is_set():
    pipeline_event("LLM", "stream_cancelled",
                   session=sid, turn_id=turn_id, tokens_discarded=token_count)
    break
```

After breaking, push `_END_OF_TOKENS` to unblock `tts_worker`, and skip history append and UI broadcast:

```python
if self._ic.cancelled.is_set():
    await self._state.token_queue.put(_END_OF_TOKENS)
    logger.info("LLM cancelled turn_id=%d tokens_discarded=%d session=%s",
                turn_id, token_count, sid)
    continue  # back to outer while loop — do NOT append history or broadcast
```

### Guard: wait for token to clear before processing next transcript

At the top of the outer `while True` loop, after dequeuing:

```python
# Wait for any in-progress interrupt cleanup to finish before starting new turn
if self._ic.cancelled.is_set():
    try:
        await asyncio.wait_for(
            self._ic.cancelled.wait_cleared(),  # see helper below
            timeout=0.5,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM: timed out waiting for interrupt clear (session=%s)", sid)
        continue
```

`wait_cleared()` is a small helper on `InterruptController`:

```python
async def wait_cleared(self) -> None:
    """Await until the CancellationToken is no longer set."""
    while self.cancelled.is_set():
        await asyncio.sleep(0.02)
```

---

## Modified: `_tts_worker`

### Register each synthesis task with `InterruptController`

```python
task = asyncio.create_task(
    self._synthesize_and_enqueue(sentence, sentence_index)
)
self._ic.register_task(task)
pending_tasks.append(task)
```

### Replace `self._state.interrupt` checks with `self._ic.cancelled.is_set()`

The existing interrupt check at the top of the token-processing loop becomes:

```python
if self._ic.cancelled.is_set():
    pipeline_event("TTS", "interrupt_drain", session=sid,
                   turn_id=self._ic.current_turn_id)
    for task in pending_tasks:
        task.cancel()
    await asyncio.gather(*pending_tasks, return_exceptions=True)
    pending_tasks.clear()
    sentence_index = 0
    self._tts.flush()
    # Drain token_queue until END_OF_TOKENS
    if token is not _END_OF_TOKENS:
        await self._drain_token_queue()
    continue
```

The `_await_and_enqueue` helper gains an additional guard:

```python
async def _await_and_enqueue(task: asyncio.Task) -> None:
    try:
        result = await task
    except asyncio.CancelledError:
        return
    except Exception as exc:
        pipeline_error("TTS", "synthesis_task_error", session=sid, error=str(exc))
        return
    if self._ic.cancelled.is_set():
        logger.debug("TTS: discarding stale WAV (cancelled) turn_id=%d session=%s",
                     self._ic.current_turn_id, sid)
        return
    if result is not None:
        wav_bytes, idx = result
        if wav_bytes:
            ...  # enqueue as before
```

---

## Modified: `_audio_output_worker`

### Replace `self._state.interrupt` check with `self._ic.cancelled.is_set()`

The existing top-of-loop interrupt check becomes:

```python
if self._ic.cancelled.is_set():
    # Drain audio_out_queue
    discarded = 0
    while not self._state.audio_out_queue.empty():
        try:
            self._state.audio_out_queue.get_nowait()
            discarded += 1
        except asyncio.QueueEmpty:
            break
    pipeline_event("AUDIO_OUT", "interrupt_flush",
                   session=sid,
                   turn_id=self._ic.current_turn_id,
                   discarded_chunks=discarded)
    # Reset per-turn counters
    first_chunk = True
    self._turn_audio_duration_ms = 0
    self._tts_turn_complete = False
    self._state.interrupt = False
    # Transition to listening and clear the CancellationToken
    await self._set_state("listening")
    self._ic.begin_turn()   # clears token, bumps turn_id
    continue
```

### Guard before sending each chunk

```python
if self._ic.cancelled.is_set():
    logger.debug("AUDIO_OUT: discarding chunk (cancelled) session=%s", sid)
    continue
```

### Guard before sending "speaking" status

```python
if first_chunk:
    if self._ic.cancelled.is_set():
        continue   # do not send "speaking" for a cancelled turn
    await self._set_state("speaking")
    first_chunk = False
```

---

## Modified: `client/audio_playback.py`

### `stop()` — add `sounddevice.stop()`

```python
def stop(self) -> None:
    """Stop playback immediately, clear the queue, and halt sounddevice."""
    self._running = False
    while not self._queue.empty():
        try:
            self._queue.get_nowait()
            self._queue.task_done()
        except asyncio.QueueEmpty:
            break
    self.stop_stream()
    logger.debug("AudioPlayback stopped and queue cleared.")

def stop_stream(self) -> None:
    """Halt the currently playing sounddevice stream immediately."""
    try:
        import sounddevice as sd
        sd.stop()
    except Exception as exc:
        logger.debug("AudioPlayback.stop_stream: %s", exc)
```

---

## Modified: `client/main.py`

### `_on_barge_in()` — stop playback before sending interrupt

```python
def _on_barge_in() -> None:
    """Stop local audio immediately, then send interrupt to server."""
    audio_playback.stop()   # synchronous — stops sounddevice immediately
    loop.call_soon_threadsafe(
        lambda: asyncio.ensure_future(ws_client.send_interrupt())
    )
```

### `_on_status()` — re-arm VAD on "listening"

```python
def _on_status(state: str) -> None:
    vad.set_speaking(state == "speaking")
    if state == "listening":
        vad.set_speaking(False)   # explicit re-arm (idempotent)
    logger.debug("Status update: %s", state)
```

---

## Interrupt Lifecycle Flow

```
User speaks during TTS playback
        │
        ▼
[Client] SileroVAD.on_barge_in()
        │
        ├─► AudioPlayback.stop()          ← local audio halted immediately
        │       └─ sounddevice.stop()
        │       └─ queue drained
        │
        └─► WSClient.send_interrupt()     ← {"type":"interrupt"} sent to server
                │
                ▼
[Server] audio_input_worker receives {"type":"interrupt"}
        │
        └─► InterruptController.request_interrupt(source="explicit_message")
                │
                ├─ cancelled.set()        ← CancellationToken set
                ├─ self._state.interrupt = True
                └─ logs INFO with turn_id + source

                ▼ (within ≤1s polling cycle)

[Server] llm_worker detects cancelled.is_set()
        │
        ├─ breaks out of token stream loop
        ├─ pushes _END_OF_TOKENS to token_queue
        └─ skips history append + UI broadcast

[Server] tts_worker detects cancelled.is_set()
        │
        ├─ task.cancel() × N pending synthesis tasks
        ├─ asyncio.gather(*tasks, return_exceptions=True)
        ├─ pending_tasks.clear(), sentence_index = 0
        ├─ TTSRouter.flush()
        └─ drains token_queue until _END_OF_TOKENS

[Server] audio_output_worker detects cancelled.is_set()
        │
        ├─ drains audio_out_queue (get_nowait loop)
        ├─ logs discarded_chunks + turn_id
        ├─ resets first_chunk, _turn_audio_duration_ms, _tts_turn_complete
        ├─ self._state.interrupt = False
        ├─ await self._set_state("listening")   ← broadcasts to client
        └─ self._ic.begin_turn()                ← clears token, bumps turn_id

                ▼

[Client] WSClient._on_status("listening")
        │
        └─► vad.set_speaking(False)       ← barge-in detection re-armed

Pipeline is now in clean listening state, ready for next utterance.
```

---

## Concurrency and Safety Properties

### No deadlocks

- `request_interrupt()` is synchronous and sets an `asyncio.Event` — no `await`, no lock.
- `begin_turn()` is synchronous — no `await`, no lock.
- `asyncio.Event.set()` and `.clear()` are thread-safe in CPython.
- `cleanup()` uses `asyncio.wait_for` with a 2-second timeout, so it never blocks indefinitely.

### Idempotency

- `request_interrupt()` returns immediately if `cancelled` is already set.
- `begin_turn()` is called exactly once per interrupt cycle, by `audio_output_worker` after flushing.
- Multiple rapid interrupts within the same turn are collapsed into one event.

### Stale chunk safety

- Every enqueue point checks `cancelled.is_set()` before putting data into a queue.
- `_await_and_enqueue` checks the token after awaiting the synthesis task, so late-completing tasks are silently discarded.
- `audio_output_worker` checks the token before sending each WAV chunk.

### Turn ID guards

- `llm_worker` calls `begin_turn()` at the start of each turn and captures the returned `turn_id` as a local variable. This local ID is used in log messages to identify stale work.
- `tts_worker` passes `sentence_index` (not turn_id) to synthesis tasks; stale-chunk detection is done via the `cancelled` event rather than turn ID comparison, which is simpler and equally correct.

---

## Components and Interfaces

This section summarises the key components introduced or modified by this feature and the interfaces between them.

### `InterruptController` (new — `server/pipeline.py`)

The central coordinator for interrupt handling. Owns the Turn ID counter and the `CancellationToken`.

| Member | Kind | Description |
|---|---|---|
| `current_turn_id` | property (int) | Read-only view of the current Turn ID |
| `cancelled` | `asyncio.Event` | CancellationToken — set on interrupt, cleared on `begin_turn()` |
| `last_interrupt_duration_ms` | property (float) | Wall-clock ms between last `request_interrupt()` and cleanup completion |
| `request_interrupt(source)` | sync method | Sets `cancelled`; idempotent; non-blocking |
| `begin_turn()` | sync method | Clears `cancelled`, increments Turn ID, clears task list; returns new ID |
| `register_task(task)` | sync method | Adds an `asyncio.Task` to the tracked synthesis task list |
| `cleanup()` | async coroutine | Cancels all tracked tasks; waits up to 2 s; clears task list |
| `wait_cleared()` | async coroutine | Polls until `cancelled` is no longer set (used by `llm_worker`) |

### `PipelineState` (modified — `server/pipeline.py`)

The shared dataclass passed to all workers. One new field is added:

| Field | Type | Description |
|---|---|---|
| `interrupt_controller` | `InterruptController \| None` | Reference to the session's `InterruptController`; set by `VoicePipeline.__init__` |

The existing `interrupt: bool` field is retained for backward compatibility and kept in sync with `InterruptController.cancelled`.

### `VoicePipeline` (modified — `server/pipeline.py`)

| Member | Change | Description |
|---|---|---|
| `__init__` | Modified | Instantiates `InterruptController`; stores as `self._ic` and on `PipelineState` |
| `stop()` | Promoted to `async` | Calls `await self._ic.cleanup()` before cancelling worker tasks |

### Worker Interfaces (modified — `server/pipeline.py`)

Each worker interacts with `InterruptController` through a narrow interface:

| Worker | Calls on IC | Checks |
|---|---|---|
| `_audio_input_worker` | `request_interrupt(source)` | — |
| `_llm_worker` | `begin_turn()` at turn start; `wait_cleared()` before dequeue | `cancelled.is_set()` per token |
| `_tts_worker` | `register_task(task)` per synthesis task | `cancelled.is_set()` before enqueue |
| `_audio_output_worker` | `begin_turn()` after flush | `cancelled.is_set()` per chunk and before "speaking" status |

### Client Components (modified)

| Component | File | Interface change |
|---|---|---|
| `AudioPlayback` | `client/audio_playback.py` | `stop()` now calls `sounddevice.stop()`; new `stop_stream()` method added |
| `SileroVAD` | `client/vad.py` | `set_speaking(False)` called on `"listening"` status to re-arm barge-in detection |
| `main.py` | `client/main.py` | `_on_barge_in()` calls `audio_playback.stop()` before `ws_client.send_interrupt()`; `_on_status()` re-arms VAD |

---

## Data Models

### `InterruptController` — Internal State

```python
class InterruptController:
    _session_id: str          # first 8 chars of session UUID, for log correlation
    _turn_id: int             # monotonically incrementing; starts at 0
    cancelled: asyncio.Event  # CancellationToken; set = interrupted, clear = active
    _synthesis_tasks: list[asyncio.Task]  # tasks registered by tts_worker
    _interrupt_start: float   # monotonic timestamp of last request_interrupt() call
    _last_duration_ms: float  # computed cleanup duration, exposed as property
```

**Invariants:**
- `_turn_id` is non-negative and strictly increases with each `begin_turn()` call.
- `cancelled` is set if and only if an interrupt has been issued for the current turn and `begin_turn()` has not yet been called.
- `_synthesis_tasks` contains only tasks from the current turn; it is cleared by `begin_turn()`.
- `_interrupt_start` is `0.0` when no interrupt is in progress.

### `PipelineState` — Modified Fields

```python
@dataclass
class PipelineState:
    # ... existing fields unchanged ...
    interrupt: bool = False                          # legacy flag; kept in sync with IC
    interrupt_controller: "InterruptController | None" = None  # NEW
```

**Invariants:**
- `interrupt_controller` is `None` only before `VoicePipeline.__init__` completes.
- `interrupt` is `True` if and only if `interrupt_controller.cancelled.is_set()` is `True` (both are set together in `request_interrupt()` and cleared together in `begin_turn()`).

### Turn Lifecycle State Machine

```
         begin_turn()
              │
              ▼
        ┌──────────┐   request_interrupt()   ┌─────────────┐
        │  ACTIVE  │ ──────────────────────► │ INTERRUPTED │
        │ (token   │                         │ (token set) │
        │ cleared) │ ◄────────────────────── │             │
        └──────────┘      begin_turn()       └─────────────┘
                       (called by audio_output_worker
                        after queue flush)
```

### Queue Ownership

| Queue | Owner | Interrupt behaviour |
|---|---|---|
| `transcript_queue` | `stt_worker` → `llm_worker` | `llm_worker` waits for token clear before dequeuing |
| `token_queue` | `llm_worker` → `tts_worker` | Drained by `tts_worker` on cancel; `_END_OF_TOKENS` sentinel unblocks drain |
| `audio_out_queue` | `tts_worker` → `audio_output_worker` | Drained by `audio_output_worker` via `get_nowait()` loop on cancel |

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Turn ID monotonicity

*For any* sequence of `begin_turn()` calls on an `InterruptController`, each call returns a value strictly greater than the previous return value.

**Validates: Requirements 1.1**

### Property 2: CancellationToken round-trip

*For any* `InterruptController`, calling `request_interrupt()` sets `cancelled`, and the subsequent call to `begin_turn()` clears `cancelled`. After `begin_turn()`, `cancelled.is_set()` is `False` regardless of how many times `request_interrupt()` was called before it.

**Validates: Requirements 1.2, 1.3, 6.3**

### Property 3: `request_interrupt()` idempotency

*For any* number N ≥ 1 of consecutive `request_interrupt()` calls on the same turn, the observable state (`cancelled.is_set()`, `_turn_id`) is identical to the state after a single call.

**Validates: Requirements 2.3, 7.1**

### Property 4: No state mutation after cancellation

*For any* worker iteration where `cancelled.is_set()` is `True` at the start of the iteration, the worker SHALL NOT enqueue any item to `token_queue`, `audio_out_queue`, or `transcript_queue`, and SHALL NOT modify conversation history.

**Validates: Requirements 1.6, 3.3, 3.4, 4.2, 4.3, 5.3, 8.1, 8.2, 8.3, 8.4, 8.5**

### Property 5: LLM stream cancellation completeness

*For any* LLM token stream of length N where `request_interrupt()` is called after token K (0 ≤ K ≤ N), the `token_queue` receives exactly the tokens produced before position K, followed by exactly one `_END_OF_TOKENS` sentinel, and no tokens at positions K…N are enqueued.

**Validates: Requirements 3.1, 3.2, 3.5**

### Property 6: TTS task cancellation completeness

*For any* set of M pending synthesis tasks registered with `InterruptController`, after `cancelled` is set and `tts_worker` performs its cleanup, all M tasks are in a cancelled or done state and `audio_out_queue` is empty.

**Validates: Requirements 4.1, 4.2, 4.6, 4.7**

### Property 7: Audio output queue flush completeness

*For any* number of WAV chunks N present in `audio_out_queue` when `audio_output_worker` detects `cancelled.is_set()`, after the flush loop completes, `audio_out_queue` is empty and exactly N chunks were discarded (not sent to the client).

**Validates: Requirements 5.1, 5.4**

### Property 8: Cleanup task cancellation with timeout

*For any* set of synthesis tasks registered with `InterruptController`, `cleanup()` cancels all tasks and completes within 2 seconds + ε, even if some tasks do not respond to cancellation.

**Validates: Requirements 9.1, 9.2, 9.3**

---

## Error Handling

### Synthesis Task Cancellation Timeout

**Scenario:** One or more synthesis tasks registered with `InterruptController` do not complete within the 2-second `cleanup()` timeout.

**Handling:** `asyncio.wait_for` raises `asyncio.TimeoutError`. The `cleanup()` coroutine catches this, logs a `WARNING` with the count of non-completing tasks and the session ID, then proceeds. The tasks are removed from `_synthesis_tasks` regardless. The pipeline continues to the next turn.

```python
except asyncio.TimeoutError:
    logger.warning(
        "IC.cleanup: %d task(s) did not finish within 2s (session=%s)",
        len(tasks), self._session_id,
    )
```

### `llm_worker` Timeout Waiting for Token Clear

**Scenario:** `audio_output_worker` has not called `begin_turn()` within 500ms after an interrupt, so `llm_worker`'s `wait_cleared()` guard times out.

**Handling:** `asyncio.wait_for` raises `asyncio.TimeoutError`. The `llm_worker` logs a `WARNING` and calls `continue` to re-enter the outer loop, re-dequeuing from `transcript_queue`. This prevents the worker from blocking indefinitely while still respecting the interrupt.

```python
except asyncio.TimeoutError:
    logger.warning("LLM: timed out waiting for interrupt clear (session=%s)", sid)
    continue
```

### `audio_output_worker` State Transition Failure

**Scenario:** The `_set_state("listening")` call fails (e.g., WebSocket is closed during cleanup).

**Handling:** Per Requirement 6.1, the transition is retried up to 3 times with a 50ms delay. If all retries fail, an `ERROR` is logged and the pipeline is left in a safe idle state. The `CancellationToken` is still cleared via `begin_turn()` so subsequent turns are not blocked.

### Stale WAV Chunk Arriving After `begin_turn()`

**Scenario:** A synthesis task completes and attempts to enqueue its WAV result after `begin_turn()` has already been called (i.e., `cancelled` is now clear but the task belongs to the previous turn).

**Handling:** The `_await_and_enqueue` helper checks `cancelled.is_set()` after awaiting the task. Because `begin_turn()` clears the token, a late-completing task from the previous turn will pass this check. The Turn ID guard in `tts_worker` (sentence_index reset) and the `audio_output_worker`'s per-chunk token check provide secondary defence. A `DEBUG` log is emitted when a stale chunk is discarded.

### `sounddevice.stop()` Failure on Client

**Scenario:** `sounddevice.stop()` raises an exception (e.g., no audio device available).

**Handling:** `AudioPlayback.stop_stream()` wraps the call in a `try/except Exception` and logs the exception at `DEBUG` level. The failure is non-fatal — the queue is still drained and `_running` is set to `False`. Playback will not resume because the worker loop checks `_running`.

```python
def stop_stream(self) -> None:
    try:
        import sounddevice as sd
        sd.stop()
    except Exception as exc:
        logger.debug("AudioPlayback.stop_stream: %s", exc)
```

### Rapid Consecutive Interrupts

**Scenario:** Multiple interrupt signals arrive within 100ms (e.g., user speaks continuously).

**Handling:** `request_interrupt()` is idempotent — the early-return guard (`if self.cancelled.is_set(): return`) ensures only the first call records `_interrupt_start` and sets the token. Subsequent calls are no-ops. The pipeline processes a single cleanup cycle and returns to `"listening"` normally.

---

## Testing Strategy

### Overview

Testing uses a dual approach: example-based unit tests for specific behaviours and edge cases, and property-based tests (using [Hypothesis](https://hypothesis.readthedocs.io/)) for universal correctness properties. The property-based tests are configured to run a minimum of 100 iterations each.

Property tests are tagged with a comment referencing the design property they validate:
```
# Feature: interrupt-barge-in-handling, Property N: <property text>
```

### Unit Tests — `InterruptController` in Isolation

These tests exercise `InterruptController` directly without any pipeline workers:

- `request_interrupt()` sets `cancelled` and is idempotent (Properties 2, 3).
- `begin_turn()` clears `cancelled`, increments `current_turn_id`, and clears `_synthesis_tasks` (Properties 1, 2).
- `cleanup()` cancels all registered tasks and drains within the 2-second timeout (Property 8).
- `cleanup()` logs a warning and proceeds when tasks exceed the 2-second timeout (Error Handling).
- `last_interrupt_duration_ms` returns a positive value after a complete interrupt cycle.
- `wait_cleared()` returns promptly after `begin_turn()` is called.

### Property-Based Tests (Hypothesis)

Each test runs ≥ 100 iterations:

| Property | Test description |
|---|---|
| Property 1 | For any N ∈ [1, 50], N calls to `begin_turn()` produce strictly increasing IDs |
| Property 2 | For any sequence of `request_interrupt()` / `begin_turn()` calls, token state matches expected |
| Property 3 | For any N ≥ 1 calls to `request_interrupt()`, state equals state after 1 call |
| Property 4 | For any worker iteration with `cancelled` set, no queue mutations occur |
| Property 5 | For any token stream length N and interrupt position K, only tokens before K are enqueued |
| Property 6 | For any M pending tasks, all are cancelled after `tts_worker` cleanup |
| Property 7 | For any N chunks in `audio_out_queue`, all are discarded after `audio_output_worker` flush |
| Property 8 | `cleanup()` always completes within 2s + ε regardless of task behaviour |

### Integration Tests (asyncio with mock queues)

These tests use real `asyncio` event loops with mock LLM/TTS backends and in-memory queues:

- Interrupt during LLM streaming: `_END_OF_TOKENS` is pushed, history is not updated.
- Interrupt during TTS synthesis: pending tasks are cancelled, `audio_out_queue` is empty after flush.
- Rapid consecutive interrupts (5 within 100ms): pipeline returns to `"listening"` within 500ms.
- Stale WAV chunk arriving after `begin_turn()`: discarded without enqueue.
- Full lifecycle: barge-in → interrupt → cleanup → `"listening"` → new turn processes correctly.

### Client-Side Tests

- `AudioPlayback.stop()` drains the queue and calls `sounddevice.stop()` (mocked).
- `AudioPlayback.stop_stream()` handles `sounddevice` exceptions gracefully.
- `_on_barge_in` calls `stop()` before `send_interrupt()` (ordering verified via mock call sequence).
- `_on_status("listening")` sets `vad._client_speaking` to `False`.

### Test Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.hypothesis]
max_examples = 100
```

---

## File Change Summary

| File | Change type | Summary |
|---|---|---|
| `server/pipeline.py` | Add class | `InterruptController` |
| `server/pipeline.py` | Modify dataclass | `PipelineState` — add `interrupt_controller` field |
| `server/pipeline.py` | Modify method | `VoicePipeline.__init__` — instantiate IC |
| `server/pipeline.py` | Modify method | `VoicePipeline.stop` — call `IC.cleanup()` |
| `server/pipeline.py` | Modify method | `_audio_input_worker` — call `IC.request_interrupt()` |
| `server/pipeline.py` | Modify method | `_llm_worker` — check `IC.cancelled`, call `begin_turn()` |
| `server/pipeline.py` | Modify method | `_tts_worker` — check `IC.cancelled`, register tasks, use `gather` for cancel |
| `server/pipeline.py` | Modify method | `_audio_output_worker` — check `IC.cancelled`, call `begin_turn()` after flush |
| `client/audio_playback.py` | Modify method | `stop()` — add `sounddevice.stop()` |
| `client/audio_playback.py` | Add method | `stop_stream()` |
| `client/main.py` | Modify function | `_on_barge_in` — call `stop()` before `send_interrupt()` |
| `client/main.py` | Modify function | `_on_status` — re-arm VAD on `"listening"` |

No new files are introduced. No routing, LLM, STT, or TTS backend files are touched.

---

<!-- Testing Strategy section appears above (before File Change Summary) -->
