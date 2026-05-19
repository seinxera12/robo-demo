# Implementation Plan: Tap to Speak Button

## Overview

Replace the "Activate Robo" toggle button with a `TapToSpeakButton` component that manages a local `micState` (`idle` | `recording`), wires into the existing `useVoiceWebSocket` hook, and respects the `VITE_AUTO_CLOSE_MIC` environment flag for auto-close behaviour on VAD silence detection.

All changes are confined to the `ui/src/` directory plus `.env` / `.env.example`. No backend files are modified.

## Tasks

- [x] 1. Add environment variable entries
  - Add `VITE_AUTO_CLOSE_MIC=true` to `.env` under the Voice Activity Detection section
  - Add `VITE_AUTO_CLOSE_MIC=true` with a descriptive comment to `.env.example`
  - _Requirements: 3.4, 3.5_

- [x] 2. Extend `useVoiceWebSocket` with optional callbacks
  - [x] 2.1 Add `UseVoiceWebSocketOptions` interface with `onRoboDeactivated?` and `onWsClose?` callbacks
    - Define the interface above the existing `UseVoiceWebSocketReturn` interface
    - Update the `useVoiceWebSocket` function signature to accept `options?: UseVoiceWebSocketOptions`
    - Call `options?.onRoboDeactivated?.()` inside the `robo_deactivated` message handler, after the existing `setRoboActiveState(false)` call
    - Call `options?.onWsClose?.()` inside the `ws.onclose` handler, after the existing `setRoboActiveState(false)` call
    - _Requirements: 4.1, 6.5_

  - [ ]* 2.2 Write unit tests for `useVoiceWebSocket` callback options
    - Test that `onRoboDeactivated` is called when a `robo_deactivated` message is received
    - Test that `onWsClose` is called when the WebSocket closes
    - Test that existing behaviour (no options passed) is unchanged
    - _Requirements: 4.1, 6.5_

- [x] 3. Create `TapToSpeakButton` component
  - [x] 3.1 Create `ui/src/components/TapToSpeakButton.tsx`
    - Export `MicState` type: `'idle' | 'recording'`
    - Define `TapToSpeakButtonProps`: `micState: MicState`, `onToggle: () => void`, `disabled?: boolean`
    - Render label "Tap to Speak" when `micState === 'idle'`, "Listening…" when `recording`
    - Set `aria-pressed={micState === 'recording'}` and `aria-label` ("Start recording" / "Stop recording")
    - Apply `animate-pulse` red dot when `micState === 'recording'`, consistent with `StatusIndicator.tsx` pattern
    - Apply `opacity-40 cursor-not-allowed` when `disabled={true}`, matching existing button disabled pattern in `App.tsx`
    - Include a mic SVG icon alongside the label
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6, 2.4_

  - [ ]* 3.2 Write unit tests for `TapToSpeakButton` in `ui/src/components/__tests__/TapToSpeakButton.test.tsx`
    - Render with `micState='idle'` → assert label "Tap to Speak" and `aria-pressed="false"`
    - Render with `micState='recording'` → assert label "Listening…", `aria-pressed="true"`, and pulse class present
    - Simulate click from `idle` → assert `onToggle` called once
    - Simulate click from `recording` → assert `onToggle` called once
    - Render with `disabled={true}` → assert button is disabled and click does not invoke `onToggle`
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 2.4_

  - [ ]* 3.3 Write property test for aria attributes (Property 1)
    - **Property 1: Aria attributes always reflect MicState**
    - For any `MicState` value, assert `aria-pressed === (micState === 'recording')` and `aria-label` is non-empty
    - **Validates: Requirements 1.6**

- [x] 4. Checkpoint — Ensure component and hook changes compile and tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Update `App.tsx` to wire `TapToSpeakButton`
  - [x] 5.1 Add `parseAutoCloseMic` pure helper and `micState` state to `App.tsx`
    - Import `TapToSpeakButton` and `MicState` from `./components/TapToSpeakButton`
    - Add `parseAutoCloseMic(value: string | undefined): boolean` — returns `false` only for exact string `"false"`, `true` otherwise
    - Add `const [micState, setMicState] = useState<MicState>('idle')`
    - Derive `const autoCloseMic = parseAutoCloseMic(import.meta.env.VITE_AUTO_CLOSE_MIC)`
    - _Requirements: 3.1, 3.2, 3.3_

  - [ ]* 5.2 Write property test for `parseAutoCloseMic` (Property 4)
    - **Property 4: AUTO_CLOSE_MIC env var parsing**
    - For any string `v`, assert `parseAutoCloseMic(v) === false` iff `v === 'false'`; all other values (including `undefined`) return `true`
    - **Validates: Requirements 3.2, 3.3**

  - [x] 5.3 Wire `useVoiceWebSocket` callbacks and `handleToggle` in `App.tsx`
    - Pass `onRoboDeactivated: () => { if (autoCloseMic) setMicState('idle'); }` to `useVoiceWebSocket`
    - Pass `onWsClose: () => setMicState('idle')` to `useVoiceWebSocket`
    - Implement `handleToggle` with `useCallback`: if `micState === 'idle'` → `setMicState('recording')` + `setRoboActive(true)`; else → `setMicState('idle')` + `setRoboActive(false)`
    - The double-open guard is implicit: `handleToggle` only calls `setRoboActive(true)` when `micState === 'idle'`
    - _Requirements: 2.1, 2.2, 2.3, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3_

  - [ ]* 5.4 Write property test for open idempotency (Property 2)
    - **Property 2: Open operation is idempotent**
    - For any number of `handleToggle` calls while `micState` is already `recording`, assert `micState` remains `recording` and `setRoboActive` is not called again
    - **Validates: Requirements 2.3**

  - [ ]* 5.5 Write property test for disabled states (Property 3)
    - **Property 3: Button is disabled for all blocking pipeline states**
    - For any `pipelineState` in `{ 'thinking', 'speaking' }`, assert `TapToSpeakButton` renders with `disabled` and `onToggle` is not invoked on click
    - **Validates: Requirements 2.4**

  - [x] 5.6 Add unmount cleanup `useEffect` and replace old button JSX in `App.tsx`
    - Add `useEffect` that returns a cleanup function calling `setRoboActive(false)` when `micState === 'recording'` at unmount
    - Replace the old `<button>` (Activate Robo) and its `onClick` handler with `<TapToSpeakButton micState={micState} onToggle={handleToggle} disabled={!canToggle} />`
    - Remove `roboActive` from the destructured hook return if it is no longer used elsewhere in `App`
    - _Requirements: 1.1, 1.2, 6.2_

  - [ ]* 5.7 Write property test for close-always-resets (Property 5)
    - **Property 5: Close always resets micState to idle**
    - For any sequence of toggle operations ending with a close (manual click while `recording`, `onRoboDeactivated` with `autoCloseMic=true`, or `onWsClose`), assert resulting `micState === 'idle'`
    - **Validates: Requirements 6.1, 6.5, 4.1**

- [x] 6. Handle mic permission denial edge case
  - In the `handleToggle` open path in `App.tsx`, wrap the `setRoboActive(true)` call in a try/catch (or use `getUserMedia` directly if needed)
  - On `NotAllowedError` / `PermissionDeniedError`, call `setMicState('idle')` and `console.warn('Microphone access denied: ', err)`
  - _Requirements: 6.3, 6.4_

  - [ ]* 6.1 Write unit test for mic permission denial
    - Mock `navigator.mediaDevices.getUserMedia` to reject with `NotAllowedError`
    - Assert `micState` resets to `idle` and `console.warn` is called
    - _Requirements: 6.3, 6.4_

- [x] 7. Install `fast-check` and create property test file
  - Install `fast-check` as a dev dependency: `npm install --save-dev fast-check` (run in `ui/`)
  - Create `ui/src/__tests__/tapToSpeak.property.test.ts` with all five property tests (Properties 1–5) using fast-check, each running a minimum of 100 iterations
  - Tag each test with the format: **Feature: tap-to-speak-button, Property {N}: {property_text}**
  - _Requirements: 1.6, 2.3, 2.4, 3.2, 3.3, 6.1, 6.5, 4.1_

- [x] 8. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- `fast-check` must be installed before running property tests (`npm install --save-dev fast-check` in `ui/`)
- The `parseAutoCloseMic` helper is exported from `App.tsx` (or a separate utils file) to make it directly importable in property tests
- Property tests are tagged with the format **Feature: tap-to-speak-button, Property {N}: {property_text}** for traceability
- The `animate-pulse` pattern for the recording state is consistent with `StatusIndicator.tsx`'s green dot on the LISTENING state
