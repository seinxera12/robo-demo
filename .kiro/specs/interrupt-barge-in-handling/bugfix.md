# Bugfix Requirements Document

## Introduction

Three related bugs in the interrupt/barge-in implementation cause spurious pipeline interruptions and incorrect LLM cancellation behaviour. Bugs 1 and 2 share the same root cause: the interrupt trigger in `_audio_input_worker` fires for any incoming PCM16 audio frame when the pipeline is in `"thinking"` or `"speaking"` state, without checking whether the user has activated voice input via the tap-to-speak button (`robo_active`). This means ambient microphone audio — speaker bleed, background noise — can interrupt an in-progress LLM generation or TTS playback even though the user never pressed the button. Bug 3 is a separate design issue: even when a barge-in is legitimate (button was pressed, user speaks during TTS playback), the current implementation cancels the LLM generation stream, which is incorrect — only TTS synthesis and audio output should be interrupted.

---

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a new PCM16 audio frame arrives at `_audio_input_worker` while the pipeline state is `"thinking"` AND `robo_active` is `False` THEN the system calls `request_interrupt()` and sets the interrupt flag, triggering a spurious pipeline interruption.

1.2 WHEN a new PCM16 audio frame arrives at `_audio_input_worker` while the pipeline state is `"speaking"` AND `robo_active` is `False` THEN the system calls `request_interrupt()` and sets the interrupt flag, causing TTS playback to be cut off by ambient audio.

1.3 WHEN `request_interrupt()` is called (from any source) while the LLM is streaming tokens in `_stream_call2_to_queue` THEN the system breaks out of the token stream loop and cancels the LLM generation, even if the interrupt was triggered by a legitimate barge-in during TTS playback.

1.4 WHEN a legitimate barge-in occurs (user presses the tap-to-speak button and speaks during TTS playback) THEN the system cancels both TTS synthesis and LLM generation, discarding tokens that could have been used for the next TTS sentence.

### Expected Behavior (Correct)

2.1 WHEN a new PCM16 audio frame arrives at `_audio_input_worker` while the pipeline state is `"thinking"` AND `robo_active` is `False` THEN the system SHALL NOT call `request_interrupt()` — the frame SHALL be enqueued to `audio_queue` without triggering an interrupt.

2.2 WHEN a new PCM16 audio frame arrives at `_audio_input_worker` while the pipeline state is `"speaking"` AND `robo_active` is `False` THEN the system SHALL NOT call `request_interrupt()` — the frame SHALL be enqueued to `audio_queue` without triggering an interrupt.

2.3 WHEN a new PCM16 audio frame arrives at `_audio_input_worker` while the pipeline state is `"thinking"` or `"speaking"` AND `robo_active` is `True` THEN the system SHALL call `request_interrupt(source="new_audio_frame")` as before.

2.4 WHEN an explicit `{"type": "interrupt"}` WebSocket message is received by `_audio_input_worker` THEN the system SHALL call `request_interrupt(source="explicit_message")` regardless of the value of `robo_active`.

2.5 WHEN `request_interrupt()` is called while the LLM is streaming tokens in `_stream_call2_to_queue` THEN the system SHALL NOT break out of the token stream loop — LLM generation SHALL continue to completion (or until the stream ends naturally).

2.6 WHEN a legitimate barge-in occurs (user presses the tap-to-speak button and speaks during TTS playback) THEN the system SHALL interrupt only the `_tts_worker` and `_audio_output_worker` — TTS synthesis tasks SHALL be cancelled and the audio output queue SHALL be flushed, but the LLM token stream SHALL continue uninterrupted.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a new PCM16 audio frame arrives at `_audio_input_worker` while the pipeline state is `"thinking"` or `"speaking"` AND `robo_active` is `True` THEN the system SHALL CONTINUE TO call `request_interrupt(source="new_audio_frame")` and trigger the barge-in flow.

3.2 WHEN an explicit `{"type": "interrupt"}` WebSocket message is received (sent by the client's `_on_barge_in` callback) THEN the system SHALL CONTINUE TO call `request_interrupt(source="explicit_message")` and trigger the interrupt flow, regardless of `robo_active`.

3.3 WHEN a barge-in interrupt is triggered THEN the `_tts_worker` SHALL CONTINUE TO cancel all pending synthesis tasks, flush the sentence buffer, and drain the token queue.

3.4 WHEN a barge-in interrupt is triggered THEN the `_audio_output_worker` SHALL CONTINUE TO drain the audio output queue, reset per-turn counters, transition to `"listening"` state, and call `begin_turn()`.

3.5 WHEN the pipeline is in `"listening"` state and `robo_active` is `False` THEN the system SHALL CONTINUE TO drop incoming audio frames silently in `_stt_worker` (the existing `audio_dropped_robo_idle` gate is unaffected).

3.6 WHEN the pipeline is in `"listening"` state THEN the system SHALL CONTINUE TO not trigger interrupts for incoming audio frames, regardless of `robo_active`.

3.7 WHEN the `_llm_worker` detects `cancelled.is_set()` after the LLM stream has completed (i.e. after `_stream_call2_to_queue` returns) THEN the system SHALL CONTINUE TO push `_END_OF_TOKENS`, skip history append, and skip UI broadcast for the cancelled turn.

---

## Bug Condition Derivation

### Bug Condition C(X) — Spurious Interrupt (Bugs 1 & 2)

```pascal
FUNCTION isBugCondition_SpuriousInterrupt(X)
  INPUT: X = (pipeline_state, robo_active, frame_source)
  OUTPUT: boolean

  // Bug fires when audio frame arrives during generation WITHOUT button press
  RETURN (pipeline_state IN {"thinking", "speaking"})
     AND (robo_active = False)
     AND (frame_source = "pcm16_audio_frame")
END FUNCTION
```

**Property: Fix Checking — Spurious Interrupt**
```pascal
FOR ALL X WHERE isBugCondition_SpuriousInterrupt(X) DO
  result ← _audio_input_worker'(X)
  ASSERT request_interrupt NOT called
  ASSERT interrupt flag NOT set
  ASSERT frame enqueued to audio_queue
END FOR
```

**Preservation Goal:**
```pascal
FOR ALL X WHERE NOT isBugCondition_SpuriousInterrupt(X) DO
  ASSERT _audio_input_worker(X) = _audio_input_worker'(X)
END FOR
// i.e. robo_active=True frames still trigger interrupt; explicit messages always trigger interrupt
```

---

### Bug Condition C(X) — LLM Cancellation During Barge-In (Bug 3)

```pascal
FUNCTION isBugCondition_LLMCancellation(X)
  INPUT: X = (interrupt_source, pipeline_stage)
  OUTPUT: boolean

  // Bug fires when interrupt arrives while LLM is mid-stream
  RETURN (interrupt_source IN {"new_audio_frame", "explicit_message"})
     AND (pipeline_stage = "llm_streaming")
END FUNCTION
```

**Property: Fix Checking — LLM Preservation During Barge-In**
```pascal
FOR ALL X WHERE isBugCondition_LLMCancellation(X) DO
  result ← _llm_worker'(X)
  ASSERT llm_stream NOT broken mid-generation
  ASSERT tokens continue flowing to token_queue
  ASSERT _END_OF_TOKENS pushed only after stream completes naturally
END FOR
```

**Preservation Goal:**
```pascal
FOR ALL X WHERE NOT isBugCondition_LLMCancellation(X) DO
  ASSERT _llm_worker(X) = _llm_worker'(X)
END FOR
// i.e. post-stream cancellation check still skips history/broadcast for cancelled turns
```
