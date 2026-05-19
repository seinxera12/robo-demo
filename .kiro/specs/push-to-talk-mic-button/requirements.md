# Requirements Document

## Introduction

Replace the existing "Activate Robo" toggle button in the browser UI with a push-to-talk (PTT) microphone button. The user clicks once to start recording and clicks again to stop; the accumulated audio is then sent to the server for STT → LLM → TTS processing. The VAD's automatic speech-end trigger is suppressed while PTT is active so the user — not silence detection — controls when the audio is submitted. Barge-in detection during assistant playback and the text-input fallback remain unaffected.

The change is intentionally minimal and localised to:
- `ui/src/App.tsx` — button UI
- `ui/src/hooks/useVoiceWebSocket.ts` — WebSocket message handling
- `client/main.py` — callback wiring
- `client/vad.py` — PTT mode flag to suppress automatic speech-end
- `client/audio_capture.py` — no structural change required (continuous capture continues; PTT state is managed in VAD)
- `server/main.py` — handle any new message types (reuse `set_active` where possible)
- `server/pipeline.py` — no change required

---

## Glossary

- **PTT_Button**: The microphone button in the browser UI that the user clicks to start and stop a recording turn.
- **PTT_Mode**: The operational state of the client-side VAD when the user has pressed the PTT_Button to begin recording. In PTT_Mode the VAD's automatic speech-end trigger is suppressed.
- **Recording_State**: The UI state while the PTT_Button has been pressed once and audio is being captured (button is active/red/pulsing).
- **Idle_State**: The UI state when no recording is in progress and the assistant is not speaking.
- **SileroVAD**: The client-side voice activity detection component (`client/vad.py`) that processes 32 ms PCM16 frames.
- **AudioCapture**: The client-side microphone capture component (`client/audio_capture.py`) that streams 32 ms PCM16 frames to SileroVAD.
- **WSClient**: The client-side WebSocket component (`client/ws_client.py`) that sends audio and control messages to the server.
- **VoicePipeline**: The server-side pipeline (`server/pipeline.py`) that gates STT processing on `robo_active`.
- **BrowserUI**: The React application served from `ui/src/`.

---

## Requirements

### Requirement 1: Push-to-Talk Button Replaces Toggle

**User Story:** As a user, I want a microphone button that I hold to record my voice, so that I have explicit control over when audio is sent to the assistant.

#### Acceptance Criteria

1. THE BrowserUI SHALL display a single microphone PTT_Button in the header in place of the existing "Activate Robo" toggle button.
2. WHEN the PTT_Button is in Idle_State and the user clicks it, THE BrowserUI SHALL transition the PTT_Button to Recording_State.
3. WHEN the PTT_Button is in Recording_State and the user clicks it, THE BrowserUI SHALL transition the PTT_Button to Idle_State and trigger audio submission.
4. WHILE the PTT_Button is in Recording_State, THE BrowserUI SHALL display a pulsing red microphone icon; both the red colour and the pulsing animation are tied to Recording_State together.
5. WHILE the PTT_Button is in Idle_State, THE BrowserUI SHALL display a non-pulsing microphone icon in a neutral colour.
6. WHEN the server sends a `robo_deactivated` message, THE BrowserUI SHALL transition the PTT_Button to Idle_State immediately, overriding any in-progress user interaction with the button.
7. WHILE the pipeline is in `thinking` or `speaking` state, THE BrowserUI SHALL disable the PTT_Button to prevent overlapping turns.

---

### Requirement 2: Client-Side PTT Mode in SileroVAD

**User Story:** As a developer, I want the VAD to suppress its automatic speech-end trigger while PTT is active, so that the user's explicit stop-click — not silence — determines when audio is submitted.

#### Acceptance Criteria

1. THE SileroVAD SHALL expose a `set_ptt_mode(enabled: bool)` method that enables or disables PTT_Mode.
2. WHILE PTT_Mode is enabled, THE SileroVAD SHALL accumulate PCM16 frames into the speech buffer without emitting an automatic `speech_end` callback based on silence detection.
3. WHEN `set_ptt_mode(False)` is called with a non-empty speech buffer, THE SileroVAD SHALL emit the `on_speech_end` callback with the accumulated buffer; state reset SHALL be attempted after the callback, and a failure to reset state SHALL not prevent the callback from completing.
4. WHILE PTT_Mode is enabled, THE SileroVAD SHALL continue to process frames for barge-in detection so that assistant playback can still be interrupted.
5. IF `set_ptt_mode(False)` is called with an empty speech buffer, THEN THE SileroVAD SHALL reset internal state without emitting `on_speech_end`.

---

### Requirement 3: Client-Side PTT Start Signal

**User Story:** As a developer, I want the client to activate PTT_Mode and notify the server when the user presses the PTT_Button, so that the server begins accepting audio.

#### Acceptance Criteria

1. WHEN the BrowserUI sends `{"type": "set_active", "active": true}` over the `/ws/ui` WebSocket, THE VoicePipeline SHALL set `robo_active = True`; IF setting `robo_active` fails due to a system error, THEN THE VoicePipeline SHALL log the error and retry the operation once before discarding the message.
2. WHEN the client receives a PTT start signal from the server-side pipeline activation, THE SileroVAD SHALL be placed into PTT_Mode via `set_ptt_mode(True)`.
3. THE WSClient SHALL reuse the existing `set_active` message type for the PTT start signal; no new message type is required for the start event.

---

### Requirement 4: Client-Side PTT Stop Signal and Audio Submission

**User Story:** As a developer, I want the client to exit PTT_Mode and submit the accumulated audio when the user releases the PTT_Button, so that the server processes exactly the audio the user intended to send.

#### Acceptance Criteria

1. WHEN the BrowserUI sends `{"type": "set_active", "active": false}` over the `/ws/ui` WebSocket, THE VoicePipeline SHALL set `robo_active = False` (existing behaviour — no server change required).
2. WHEN the BrowserUI transitions the PTT_Button from Recording_State to Idle_State, THE useVoiceWebSocket hook SHALL send `{"type": "ptt_stop"}` over the `/ws/ui` WebSocket; IF the WebSocket connection is unavailable or the send fails, THE useVoiceWebSocket hook SHALL accept the loss of that PTT session without retry.
3. WHEN the server receives `{"type": "ptt_stop"}`, THE server SHALL forward a `ptt_stop` signal to the active AudioClient pipeline via a new `ptt_stop` message sent over the `/ws` (AudioClient) WebSocket.
4. WHEN the AudioClient receives a `ptt_stop` message over the `/ws` WebSocket, THE client `main.py` SHALL call `vad.set_ptt_mode(False)` to flush the buffer and trigger `on_speech_end`.
5. WHEN `on_speech_end` fires after a PTT stop, THE WSClient SHALL send the accumulated PCM16 audio to the server via the existing `send_audio` method (no change to audio submission protocol).

---

### Requirement 5: Server Routing of PTT Stop Signal

**User Story:** As a developer, I want the server to relay the PTT stop signal from the BrowserUI WebSocket to the AudioClient WebSocket, so that the client-side VAD knows when to flush its buffer.

#### Acceptance Criteria

1. WHEN the `/ws/ui` endpoint receives `{"type": "ptt_stop"}`, THE server SHALL look up the most recently active AudioClient pipeline and send `{"type": "ptt_stop"}` as a text message over that pipeline's `/ws` WebSocket.
2. IF no active AudioClient pipeline exists when `ptt_stop` is received, THEN THE server SHALL log a debug message and take no further action.
3. THE server `_audio_input_worker` in VoicePipeline SHALL parse incoming text messages on the `/ws` WebSocket and, WHEN `{"type": "ptt_stop"}` is received, SHALL call `pipeline.trigger_ptt_stop()`.
4. THE VoicePipeline SHALL expose a `trigger_ptt_stop()` method that sends a `ptt_stop` signal to the client-side VAD via a dedicated asyncio Event or queue.

---

### Requirement 6: Barge-In Preservation

**User Story:** As a user, I want to be able to interrupt the assistant while it is speaking, so that the conversation feels natural even with push-to-talk.

#### Acceptance Criteria

1. WHILE the assistant is in `speaking` state and PTT_Mode is disabled, THE SileroVAD SHALL detect barge-in events; detection and the `on_barge_in` callback are not required to be atomic — detection may succeed without the callback firing in edge cases.
2. WHILE PTT_Mode is enabled, THE SileroVAD SHALL still detect barge-in events and call `on_barge_in` if the client is in `speaking` state.
3. THE existing barge-in cooldown and debounce guards in SileroVAD SHALL remain unchanged.

---

### Requirement 7: Text Input Fallback Independence

**User Story:** As a user, I want to type messages even when the PTT_Button is in Idle_State, so that I can use the assistant without a microphone.

#### Acceptance Criteria

1. THE BrowserUI text input field SHALL remain functional and independent of the PTT_Button state at all times, including when the PTT_Button is in Idle_State.
2. WHEN the user submits text via the text input, THE useVoiceWebSocket hook SHALL send `{"type": "text_input", "text": "..."}` as before, regardless of PTT_Button state.
3. THE server text_input routing path SHALL be unchanged by this feature.

---

### Requirement 8: Accessibility

**User Story:** As a user relying on assistive technology, I want the PTT_Button to communicate its state clearly, so that I can use the voice feature with a screen reader.

#### Acceptance Criteria

1. THE PTT_Button SHALL have an `aria-label` of `"Start recording"` when in Idle_State.
2. THE PTT_Button SHALL have an `aria-label` of `"Stop recording"` when in Recording_State.
3. THE PTT_Button SHALL have `aria-pressed` set to `false` when in Idle_State and `true` when in Recording_State.
4. WHEN the PTT_Button is disabled (pipeline thinking or speaking), THE PTT_Button SHALL have `aria-disabled="true"` and `disabled` attribute set.
