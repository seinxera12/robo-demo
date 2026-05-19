# Requirements Document

## Introduction

This feature replaces the existing "Activate Robo" toggle button in the React UI (`ui/src/`) with a "Tap to Speak" microphone button. The button controls mic open/close directly from the browser UI, provides two visually distinct states (idle and recording), and supports an environment-gated `AUTO_CLOSE_MIC` flag that determines whether VAD silence detection automatically stops the mic and fires submission. No backend logic, VAD internals, transcription pipeline, audio processing, or response generation is modified.

## Glossary

- **TapToSpeakButton**: The new React UI button component that replaces the "Activate Robo" button. Renders in the header of `App.tsx`.
- **MicState**: The two possible states of the button — `idle` (mic off, default) and `recording` (mic live, audio capture active).
- **AUTO_CLOSE_MIC**: A boolean environment variable (prefixed `VITE_` for Vite/React exposure) that gates whether VAD silence detection automatically stops the mic and fires submission.
- **VAD**: Voice Activity Detection — the existing `SileroVAD` logic in `client/vad.py`. Not modified by this feature.
- **Submission**: The action of sending accumulated audio to the server, equivalent to the existing `on_speech_end` / `set_active` flow that currently fires on VAD silence detection.
- **useVoiceWebSocket**: The existing React hook in `ui/src/hooks/useVoiceWebSocket.ts` that manages the WebSocket connection and exposes `setRoboActive`.
- **App**: The root React component in `ui/src/App.tsx`.

---

## Requirements

### Requirement 1: Replace "Activate Robo" Button with "Tap to Speak" Button

**User Story:** As a user, I want a clearly labelled "Tap to Speak" button in the UI header, so that I can intuitively start and stop voice input without confusion about what the button does.

#### Acceptance Criteria

1. THE **App** SHALL render a **TapToSpeakButton** in the same header position previously occupied by the "Activate Robo" button.
2. THE **App** SHALL remove the "Activate Robo" button and its associated `onClick` handler entirely.
3. WHEN **MicState** is `idle`, THE **TapToSpeakButton** SHALL display the label "Tap to Speak".
4. WHEN **MicState** is `recording`, THE **TapToSpeakButton** SHALL display the label "Listening…".
5. WHEN **MicState** is `recording`, THE **TapToSpeakButton** SHALL apply a visually distinct style (e.g., pulsing animation, color change, or icon swap) that is consistent with the existing Tailwind CSS and animation patterns already used in the codebase.
6. THE **TapToSpeakButton** SHALL be accessible, providing an `aria-pressed` attribute reflecting the current **MicState** and an `aria-label` that describes the current action.

---

### Requirement 2: Toggle Mic On/Off Behavior

**User Story:** As a user, I want a single button click to start or stop mic capture, so that I have explicit control over when the microphone is active.

#### Acceptance Criteria

1. WHEN the **TapToSpeakButton** is clicked and **MicState** is `idle`, THE **TapToSpeakButton** SHALL transition **MicState** to `recording` and call `setRoboActive(true)` via **useVoiceWebSocket**.
2. WHEN the **TapToSpeakButton** is clicked and **MicState** is `recording`, THE **TapToSpeakButton** SHALL transition **MicState** to `idle`, call `setRoboActive(false)` via **useVoiceWebSocket**, and immediately trigger submission — equivalent to the existing VAD silence-detection submission flow.
3. WHILE **MicState** is `recording` and another activation call is triggered (e.g., a rapid double-click), THE **TapToSpeakButton** SHALL guard against double-initialization and ignore the redundant open call.
4. WHEN `pipelineState` is `thinking` or `speaking`, THE **TapToSpeakButton** SHALL be disabled and not respond to clicks, consistent with the existing `canToggle` guard in **App**.

---

### Requirement 3: AUTO_CLOSE_MIC Environment Variable

**User Story:** As a developer, I want an environment flag to control whether VAD silence detection auto-closes the mic, so that I can disable auto-close during testing without modifying code.

#### Acceptance Criteria

1. THE **App** SHALL read a `VITE_AUTO_CLOSE_MIC` environment variable using `import.meta.env.VITE_AUTO_CLOSE_MIC`, consistent with the Vite `import.meta.env` pattern used in the project.
2. WHEN `VITE_AUTO_CLOSE_MIC` is absent or set to `"true"`, THE **App** SHALL treat `AUTO_CLOSE_MIC` as `true`.
3. WHEN `VITE_AUTO_CLOSE_MIC` is set to `"false"`, THE **App** SHALL treat `AUTO_CLOSE_MIC` as `false`.
4. THE `.env` file SHALL include a `VITE_AUTO_CLOSE_MIC=true` entry under the Voice Activity Detection section.
5. THE `.env.example` file SHALL include a `VITE_AUTO_CLOSE_MIC=true` entry with a descriptive comment explaining its purpose.

---

### Requirement 4: Auto-Close on VAD Silence Detection (AUTO_CLOSE_MIC=true)

**User Story:** As a user, I want the mic to automatically stop and submit after I stop speaking, so that I don't have to manually click the button after every utterance.

#### Acceptance Criteria

1. WHEN `AUTO_CLOSE_MIC` is `true` and the server emits a `robo_deactivated` WebSocket message (signalling end-of-turn / VAD silence), THE **useVoiceWebSocket** hook SHALL reset **MicState** to `idle`.
2. WHEN `AUTO_CLOSE_MIC` is `true` and the server emits `robo_deactivated`, THE **TapToSpeakButton** SHALL visually return to the idle state ("Tap to Speak", no pulse) without requiring a user click.
3. WHEN `AUTO_CLOSE_MIC` is `true` and the server emits `robo_deactivated`, THE **App** SHALL fire submission — the same flow as the existing `robo_deactivated` handler in **useVoiceWebSocket**.

---

### Requirement 5: No Auto-Close When AUTO_CLOSE_MIC=false

**User Story:** As a developer, I want the mic to stay open after VAD silence detection when AUTO_CLOSE_MIC is false, so that I can test and debug without the mic being interrupted by silence.

#### Acceptance Criteria

1. WHEN `AUTO_CLOSE_MIC` is `false` and the server emits `robo_deactivated`, THE **App** SHALL NOT reset **MicState** to `idle`.
2. WHEN `AUTO_CLOSE_MIC` is `false` and the server emits `robo_deactivated`, THE **TapToSpeakButton** SHALL remain in the `recording` state and continue displaying "Listening…".
3. WHEN `AUTO_CLOSE_MIC` is `false`, THE **App** SHALL NOT fire submission on VAD silence detection — submission is only triggered by a manual button click.

---

### Requirement 6: Cleanup and Edge Cases

**User Story:** As a user, I want the button to always reflect the true mic state and never get stuck in "Listening…", so that I can trust the UI and avoid confusion.

#### Acceptance Criteria

1. WHEN the mic is closed (manually or via auto-close), THE **TapToSpeakButton** SHALL reliably reset to `idle` state — no stuck "Listening…" label or recording style.
2. WHEN the **App** component unmounts while **MicState** is `recording`, THE **App** SHALL call `setRoboActive(false)` to tear down the mic cleanly before unmount.
3. IF the browser or OS denies microphone access, THEN THE **TapToSpeakButton** SHALL reset **MicState** to `idle` and log a `console.warn` message describing the denial.
4. IF the browser or OS denies microphone access, THEN THE **TapToSpeakButton** SHALL NOT remain in the `recording` state after the denial.
5. WHEN the WebSocket connection is lost (server `onclose` event), THE **useVoiceWebSocket** hook SHALL reset **MicState** to `idle`, consistent with the existing `setRoboActiveState(false)` call in the `onclose` handler.
