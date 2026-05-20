# Interrupt / Barge-In Handling Bugfix Design

## Overview

Three bugs in `server/pipeline.py` cause incorrect interrupt behaviour. Bugs 1 and 2 share a
single root cause: the PCM16-triggered interrupt in `_audio_input_worker` fires without checking
`robo_active`, so ambient microphone audio (speaker bleed, background noise) can interrupt an
in-progress LLM generation or TTS playback even though the user never pressed the tap-to-speak
button. Bug 3 is a separate design issue: when a legitimate barge-in occurs, the LLM streaming
loop breaks mid-generation, discarding tokens that the TTS worker could have used for the next
sentence.

Both fixes are surgical one-line or few-line changes. No architectural changes are required.

---

## Glossary

- **Bug_Condition (C)**: The condition that triggers the defective behaviour — defined separately
  for the spurious-interrupt bug and the LLM-cancellation bug.
- **Property (P)**: The desired correct behaviour when the bug condition holds.
- **Preservation**: Existing correct behaviour that must remain unchanged after the fix.
- **`robo_active`**: `PipelineState.robo_active` — `True` only when the user has pressed the
  tap-to-speak button. Managed by `VoicePipeline.set_robo_active()`.
- **`_audio_input_worker`**: Worker 1 in `server/pipeline.py`. Receives binary WebSocket frames
  from the AudioClient. Responsible for routing PCM16 audio to `audio_queue` and for triggering
  barge-in interrupts.
- **`_stream_call2_to_queue`**: Nested async function inside `_llm_worker`. Streams LLM tokens
  to `token_queue`. Returns the assembled full response string.
- **`_ic` / `InterruptController`**: Owns the `cancelled` asyncio.Event and the current turn ID.
  `request_interrupt()` sets `cancelled`; `begin_turn()` clears it.
- **`_END_OF_TOKENS`**: Sentinel object pushed to `token_queue` to signal end-of-stream.
- **`_drain_token_queue()`**: Helper that drains `token_queue` until `_END_OF_TOKENS` is seen.
- **`_tts_worker`**: Worker 4. Drains `token_queue`, accumulates tokens to sentence boundaries,
  and synthesises WAV. Checks `cancelled.is_set()` at the top of its loop and calls
  `_drain_token_queue()` when interrupted.

---

## Bug Details

### Bug Condition C₁ — Spurious Interrupt (Bugs 1 & 2)

The bug manifests when a PCM16 audio frame arrives at `_audio_input_worker` while the pipeline
is in `"thinking"` or `"speaking"` state and `robo_active` is `False`. The interrupt condition
lacks the `robo_active` gate, so any ambient microphone audio triggers a barge-in.

**Formal Specification:**
```
FUNCTION isBugCondition_SpuriousInterrupt(X)
  INPUT:  X = (pipeline_state, robo_active, frame_source)
  OUTPUT: boolean

  RETURN (pipeline_state IN {"thinking", "speaking"})
     AND (robo_active = False)
     AND (frame_source = "pcm16_audio_frame")
END FUNCTION
```

**Buggy code (current):**
```python
if self._state.state in ("thinking", "speaking"):
    self._ic.request_interrupt(source="new_audio_frame")
    self._state.interrupt = True
await self._state.audio_queue.put(raw_bytes)
```

**Fixed code:**
```python
if self._state.state in ("thinking", "speaking") and self._state.robo_active:
    self._ic.request_interrupt(source="new_audio_frame")
    self._state.interrupt = True
await self._state.audio_queue.put(raw_bytes)
```

**Concrete examples:**

| Scenario | pipeline_state | robo_active | frame_source | Buggy behaviour | Correct behaviour |
|---|---|---|---|---|---|
| Speaker bleed during TTS | `"speaking"` | `False` | pcm16 | Interrupt fired — TTS cut off | No interrupt — frame enqueued |
| Background noise during LLM | `"thinking"` | `False` | pcm16 | Interrupt fired — LLM cancelled | No interrupt — frame enqueued |
| User presses button, speaks | `"speaking"` | `True` | pcm16 | Interrupt fired ✓ | Interrupt fired ✓ (unchanged) |
| Explicit interrupt message | any | any | `{"type":"interrupt"}` | Interrupt fired ✓ | Interrupt fired ✓ (unchanged) |

---

### Bug Condition C₂ — LLM Cancellation During Barge-In (Bug 3)

The bug manifests when `cancelled.is_set()` becomes `True` while `_stream_call2_to_queue` is
iterating over the LLM token stream. The mid-loop check breaks the stream early, discarding
tokens that the TTS worker could have consumed.

**Formal Specification:**
```
FUNCTION isBugCondition_LLMCancellation(X)
  INPUT:  X = (interrupt_source, pipeline_stage)
  OUTPUT: boolean

  RETURN (interrupt_source IN {"new_audio_frame", "explicit_message"})
     AND (pipeline_stage = "llm_streaming")
END FUNCTION
```

**Buggy code (current) — inside `_stream_call2_to_queue`:**
```python
async for token in self._llm.stream(messages, max_tokens=200, temperature=0.65):
    if self._ic.cancelled.is_set():          # ← BUG: breaks stream early
        pipeline_event("LLM", "stream_interrupted",
                       session=sid, tokens_so_far=token_count)
        break
    ...
    await self._state.token_queue.put(token)
```

**Fixed code — remove the mid-loop check entirely:**
```python
async for token in self._llm.stream(messages, max_tokens=200, temperature=0.65):
    if not first_token_logged:
        ttft_ms = int((time.monotonic() - self._speech_end_time) * 1000)
        pipeline_event("LLM", "call2_first_token", session=sid, ttft_ms=ttft_ms)
        first_token_logged = True

    await self._state.token_queue.put(token)
    parts.append(token)
    token_count += 1
```

**Concrete examples:**

| Scenario | cancelled set at | Buggy behaviour | Correct behaviour |
|---|---|---|---|
| Barge-in at token 5 of 20 | token 5 | Stream breaks; tokens 6–20 lost | Stream continues; all 20 tokens enqueued |
| No barge-in | never | All tokens enqueued ✓ | All tokens enqueued ✓ (unchanged) |
| Barge-in after stream ends | post-stream | Post-stream check skips history ✓ | Post-stream check skips history ✓ (unchanged) |

---

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- When `robo_active` is `True` and a PCM16 frame arrives during `"thinking"` or `"speaking"`,
  `request_interrupt(source="new_audio_frame")` MUST still be called.
- Explicit `{"type": "interrupt"}` WebSocket messages MUST continue to trigger
  `request_interrupt(source="explicit_message")` regardless of `robo_active`.
- The `_tts_worker` interrupt drain path (cancel pending synthesis tasks, flush sentence buffer,
  call `_drain_token_queue()`) MUST remain unchanged.
- The `_audio_output_worker` interrupt clear path (drain `audio_out_queue`, advance turn counter,
  reset flags, transition to `"listening"`) MUST remain unchanged.
- The post-stream `if self._ic.cancelled.is_set():` check in `_llm_worker` (push `_END_OF_TOKENS`,
  log discarded tokens, skip history append and UI broadcast) MUST remain unchanged.
- The `_stt_worker` `robo_active` gate (drop audio silently when `robo_active` is `False`) is
  unaffected by either fix.

**Scope:**
All inputs that do NOT satisfy `isBugCondition_SpuriousInterrupt` or `isBugCondition_LLMCancellation`
must be completely unaffected. This includes:
- PCM16 frames arriving while `robo_active` is `True`
- Explicit interrupt messages (any `robo_active` value)
- PCM16 frames arriving while the pipeline is in `"listening"` state
- LLM token streams that complete without any interrupt

---

## Hypothesized Root Cause

### Root Cause 1 — Missing `robo_active` Gate (Bugs 1 & 2)

The interrupt condition in `_audio_input_worker` was written before the `robo_active` concept
was introduced. When `robo_active` was added to gate the STT worker, the corresponding gate was
not added to the interrupt trigger. The fix is a single `and self._state.robo_active` appended
to the existing condition.

**Why the explicit-message path is intentionally ungated:** The explicit `{"type": "interrupt"}`
message is sent by the client's `_on_barge_in` callback, which is only invoked when the user
actively presses the barge-in button. It is already gated at the client side; no server-side
`robo_active` check is needed or desired.

### Root Cause 2 — Incorrect Interrupt Scope in LLM Worker (Bug 3)

The `cancelled` event was designed to signal TTS and audio output workers to stop playback. The
LLM worker was incorrectly also checking it mid-stream, treating a TTS-level interrupt as a
reason to abort LLM generation. The correct design is:

- **TTS worker**: checks `cancelled` → drains token queue, cancels synthesis tasks.
- **Audio output worker**: checks `cancelled` → drains audio queue, resets state.
- **LLM worker**: does NOT check `cancelled` mid-stream → streams all tokens to completion.
  Checks `cancelled` only AFTER the stream ends to decide whether to store history and broadcast.

The `_tts_worker` already calls `_drain_token_queue()` when interrupted, so any tokens the LLM
continues to produce after a barge-in are safely consumed and discarded by the TTS worker.

---

## Correctness Properties

Property 1: Bug Condition — Spurious Interrupt Suppressed

_For any_ input `X = (pipeline_state, robo_active, frame_source)` where
`isBugCondition_SpuriousInterrupt(X)` returns `True` (i.e. `pipeline_state ∈ {"thinking",
"speaking"}`, `robo_active = False`, `frame_source = "pcm16_audio_frame"`), the fixed
`_audio_input_worker` SHALL NOT call `request_interrupt()`, SHALL NOT set `self._state.interrupt`,
and SHALL enqueue the frame to `audio_queue`.

**Validates: Requirements bugfix.md 2.1, 2.2**

Property 2: Preservation — Legitimate Barge-In Still Fires

_For any_ input `X = (pipeline_state, robo_active, frame_source)` where
`isBugCondition_SpuriousInterrupt(X)` returns `False` AND `robo_active = True` AND
`pipeline_state ∈ {"thinking", "speaking"}`, the fixed `_audio_input_worker` SHALL call
`request_interrupt(source="new_audio_frame")` and set `self._state.interrupt = True`, identical
to the original function.

**Validates: Requirements bugfix.md 2.3, 3.1**

Property 3: Preservation — Explicit Interrupt Always Fires

_For any_ input where `frame_source = "explicit_message"` (i.e. a `{"type": "interrupt"}` JSON
message), the fixed `_audio_input_worker` SHALL call `request_interrupt(source="explicit_message")`
regardless of `robo_active`, identical to the original function.

**Validates: Requirements bugfix.md 2.4, 3.2**

Property 4: Bug Condition — LLM Stream Runs to Completion During Barge-In

_For any_ token stream of length N where `cancelled` is set at position K (0 ≤ K < N), the fixed
`_stream_call2_to_queue` SHALL enqueue all N tokens to `token_queue` (not just K tokens). The
stream SHALL NOT break early.

**Validates: Requirements bugfix.md 2.5, 2.6**

Property 5: Preservation — Post-Stream Cancellation Check Unchanged

_For any_ scenario where `cancelled.is_set()` is `True` after `_stream_call2_to_queue` returns,
the fixed `_llm_worker` SHALL push `_END_OF_TOKENS` to `token_queue`, log the discarded token
count, skip history append, and skip UI broadcast — identical to the original post-stream check.

**Validates: Requirements bugfix.md 3.7**

---

## Fix Implementation

### Fix 1 — `_audio_input_worker` (Bugs 1 & 2)

**File:** `server/pipeline.py`

**Function:** `_audio_input_worker`

**Change:** Add `and self._state.robo_active` to the PCM16-triggered interrupt condition.

```python
# BEFORE
if self._state.state in ("thinking", "speaking"):
    self._ic.request_interrupt(source="new_audio_frame")
    self._state.interrupt = True

# AFTER
if self._state.state in ("thinking", "speaking") and self._state.robo_active:
    self._ic.request_interrupt(source="new_audio_frame")
    self._state.interrupt = True
```

**What does NOT change:**
- The `await self._state.audio_queue.put(raw_bytes)` line immediately after — frames are always
  enqueued regardless of interrupt.
- The explicit `{"type": "interrupt"}` handling block above this condition — it remains ungated.
- The text-path `{"type": "interrupt"}` handling in the `elif raw_text` branch — also unchanged.

---

### Fix 2 — `_stream_call2_to_queue` inside `_llm_worker` (Bug 3)

**File:** `server/pipeline.py`

**Function:** `_stream_call2_to_queue` (nested inside `_llm_worker`)

**Change:** Remove the `if self._ic.cancelled.is_set(): break` block from inside the
`async for token` loop.

```python
# BEFORE (inside async for token loop)
if self._ic.cancelled.is_set():
    pipeline_event("LLM", "stream_interrupted",
                   session=sid, tokens_so_far=token_count)
    break

# AFTER — these lines are deleted entirely
```

**What does NOT change:**
- The post-stream check in `_llm_worker` (after `_stream_call2_to_queue` returns):
  ```python
  if self._ic.cancelled.is_set():
      token_count = len(full_response.split()) if full_response else 0
      await self._state.token_queue.put(_END_OF_TOKENS)
      logger.info("LLM cancelled turn_id=%d tokens_discarded=%d session=%s", ...)
      continue
  ```
  This block is correct and must be kept.
- The `_tts_worker` interrupt drain path — unchanged.
- The `_audio_output_worker` interrupt clear path — unchanged.

---

### How the Fixes Interact: Barge-In Token Flow After Fix 2

After Fix 2, when a legitimate barge-in occurs (user presses button, speaks during TTS):

1. `_audio_input_worker` calls `request_interrupt(source="new_audio_frame")` — sets `cancelled`.
2. **`_tts_worker`** detects `cancelled.is_set()` at the top of its loop:
   - Cancels all pending synthesis tasks.
   - Calls `self._tts.flush()` to clear the sentence accumulator.
   - Calls `_drain_token_queue()` — drains all tokens from `token_queue` until `_END_OF_TOKENS`.
3. **`_llm_worker`** (`_stream_call2_to_queue`) continues streaming tokens into `token_queue`
   uninterrupted. The TTS worker's `_drain_token_queue()` consumes and discards them.
4. When the LLM stream ends naturally, `_stream_call2_to_queue` pushes `_END_OF_TOKENS` (via the
   normal end-of-stream path in `_llm_worker`). The TTS worker's drain loop exits on this sentinel.
5. Back in `_llm_worker`, the post-stream `if self._ic.cancelled.is_set():` check fires:
   - Pushes `_END_OF_TOKENS` (belt-and-suspenders, in case the drain already consumed it).
   - Logs discarded token count.
   - Skips history append and UI broadcast.
   - `continue`s to the next transcript.
6. **`_audio_output_worker`** drains `audio_out_queue`, advances the turn counter, resets flags,
   and transitions to `"listening"`.

The key invariant: `_drain_token_queue()` in the TTS worker acts as the consumer for any tokens
the LLM produces after the interrupt. No tokens are lost or leaked; the queue is always drained
to `_END_OF_TOKENS`.

---

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate
each bug on unfixed code, then verify the fix works correctly and preserves existing behaviour.

---

### Exploratory Bug Condition Checking

**Goal:** Surface counterexamples that demonstrate the bugs BEFORE implementing the fixes.
Confirm or refute the root cause analysis.

**Test Plan:** Write unit tests that mock `InterruptController` and `PipelineState`, then call
the relevant code paths directly. Run on UNFIXED code to observe failures.

**Test Cases for Bug 1 & 2 (Spurious Interrupt):**
1. **Ambient audio during thinking, robo_active=False**: Simulate a PCM16 frame arriving while
   `state="thinking"` and `robo_active=False`. Assert `request_interrupt` is NOT called.
   (Will fail on unfixed code — interrupt IS called.)
2. **Ambient audio during speaking, robo_active=False**: Same as above with `state="speaking"`.
   (Will fail on unfixed code.)
3. **Explicit interrupt, robo_active=False**: Simulate `{"type":"interrupt"}` binary message.
   Assert `request_interrupt` IS called. (Should pass on both unfixed and fixed code.)

**Test Cases for Bug 3 (LLM Cancellation):**
1. **Cancelled mid-stream at token K**: Mock `self._llm.stream()` to yield N tokens. Set
   `cancelled` after token K. Assert all N tokens are in `token_queue`. (Will fail on unfixed
   code — only K tokens are enqueued.)
2. **No cancellation**: Mock stream yields N tokens, `cancelled` never set. Assert all N tokens
   enqueued. (Should pass on both unfixed and fixed code.)

**Expected Counterexamples:**
- `request_interrupt` is called when `robo_active=False` and a PCM16 frame arrives during generation.
- Only K tokens (not N) are enqueued when `cancelled` is set at position K.

---

### Fix Checking

**Goal:** Verify that for all inputs where the bug condition holds, the fixed function produces
the expected behaviour.

**Pseudocode — Spurious Interrupt Fix:**
```
FOR ALL X WHERE isBugCondition_SpuriousInterrupt(X) DO
  result ← _audio_input_worker_fixed(X)
  ASSERT request_interrupt NOT called
  ASSERT interrupt flag NOT set
  ASSERT frame enqueued to audio_queue
END FOR
```

**Pseudocode — LLM Cancellation Fix:**
```
FOR ALL X = (token_stream_length=N, cancelled_at=K) WHERE K < N DO
  result ← _stream_call2_to_queue_fixed(X)
  ASSERT token_queue.qsize() == N
  ASSERT result == joined(all N tokens)
END FOR
```

---

### Preservation Checking

**Goal:** Verify that for all inputs where the bug condition does NOT hold, the fixed function
produces the same result as the original function.

**Pseudocode — Spurious Interrupt Preservation:**
```
FOR ALL X WHERE NOT isBugCondition_SpuriousInterrupt(X) DO
  ASSERT _audio_input_worker_original(X) = _audio_input_worker_fixed(X)
END FOR
// Covers: robo_active=True frames, explicit interrupt messages, listening-state frames
```

**Pseudocode — LLM Cancellation Preservation:**
```
FOR ALL X WHERE NOT isBugCondition_LLMCancellation(X) DO
  ASSERT _stream_call2_to_queue_original(X) = _stream_call2_to_queue_fixed(X)
END FOR
// Covers: streams that complete without cancellation
```

**Testing Approach:** Property-based testing (Hypothesis) is recommended for preservation
checking because it generates many input combinations automatically and catches edge cases
(e.g. `cancelled` set before the first token, empty streams, single-token streams).

---

### Unit Tests

- Test `_audio_input_worker` with `robo_active=False`, `state="thinking"` → no interrupt.
- Test `_audio_input_worker` with `robo_active=False`, `state="speaking"` → no interrupt.
- Test `_audio_input_worker` with `robo_active=True`, `state="thinking"` → interrupt fired.
- Test `_audio_input_worker` with `robo_active=True`, `state="speaking"` → interrupt fired.
- Test `_audio_input_worker` with explicit `{"type":"interrupt"}` message, `robo_active=False`
  → interrupt fired (ungated path).
- Test `_stream_call2_to_queue` with `cancelled` set at token K of N → all N tokens enqueued.
- Test `_stream_call2_to_queue` with no cancellation → all tokens enqueued, correct return value.
- Test post-stream cancellation check → `_END_OF_TOKENS` pushed, history skipped.

### Property-Based Tests

- **Property 1 (Spurious Interrupt):** For any `(state ∈ {"thinking","speaking"}, robo_active=False,
  n_frames ∈ [1..50])`, after processing n_frames PCM16 frames, `request_interrupt` call count = 0.
- **Property 2 (Legitimate Barge-In):** For any `(state ∈ {"thinking","speaking"}, robo_active=True,
  n_frames ∈ [1..50])`, after processing n_frames PCM16 frames, `request_interrupt` call count ≥ 1
  (idempotent after first call).
- **Property 3 (LLM Stream Completeness):** For any token stream of length N (1..100) and
  cancellation position K (0..N), all N tokens appear in `token_queue` after the fixed
  `_stream_call2_to_queue` returns.
- **Property 4 (Post-Stream Preservation):** For any scenario where `cancelled` is set after the
  stream ends, the post-stream check pushes exactly one `_END_OF_TOKENS` and skips history.

### Integration Tests

- Manually verify: with `robo_active=False`, ambient audio during TTS playback does NOT interrupt.
- Manually verify: with `robo_active=True`, speaking during TTS playback triggers interrupt, stops
  TTS, but LLM continues streaming (tokens drained by TTS worker).
- Manually verify: explicit `{"type":"interrupt"}` message still triggers interrupt regardless of
  `robo_active`.
- Manually verify: after a barge-in, the pipeline correctly transitions back to `"listening"` and
  accepts the next user turn.

---

## Regression Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Explicit interrupt path accidentally gated by `robo_active` | Low — it's a separate code block | Unit test: explicit interrupt fires with `robo_active=False` |
| `_drain_token_queue` races with LLM still producing tokens | Low — asyncio single-threaded; drain loops until `_END_OF_TOKENS` | Property test: all N tokens consumed before drain exits |
| Post-stream `_END_OF_TOKENS` pushed twice (once by LLM, once by post-stream check) | Low — TTS drain already consumed the first one | Existing `_drain_token_queue` handles multiple sentinels gracefully (breaks on first) |
| `robo_active` gate prevents legitimate barge-in when button is held | None — `robo_active=True` path is unchanged | Unit test: `robo_active=True` still fires interrupt |
| Removing mid-stream log `"stream_interrupted"` loses observability | Low — post-stream log `"LLM cancelled"` still fires | Acceptable trade-off; post-stream log includes `tokens_discarded` count |
