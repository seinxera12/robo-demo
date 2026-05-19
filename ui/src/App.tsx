import { useCallback, useEffect, useState } from 'react';
import StatusIndicator from './components/StatusIndicator';
import TapToSpeakButton, { MicState } from './components/TapToSpeakButton';
import TranscriptDisplay from './components/TranscriptDisplay';
import TextInput from './components/TextInput';
import { useVoiceWebSocket } from './hooks/useVoiceWebSocket';

/**
 * Parse the VITE_AUTO_CLOSE_MIC environment variable.
 * Returns false only when the value is the exact string "false".
 * All other values (including undefined / absent) return true.
 */
export function parseAutoCloseMic(value: string | undefined): boolean {
  return value !== 'false';
}

const autoCloseMic = parseAutoCloseMic(import.meta.env.VITE_AUTO_CLOSE_MIC);

export default function App() {
  const [micState, setMicState] = useState<MicState>('idle');

  const {
    pipelineState,
    messages,
    currentAssistantText,
    sendTextInput,
    setRoboActive,
    isLlmGenerating,
  } = useVoiceWebSocket({
    onRoboDeactivated: () => {
      // Auto-close the mic when the server signals end-of-turn, but only
      // when AUTO_CLOSE_MIC is enabled (default: true).
      if (autoCloseMic) setMicState('idle');
    },
    onWsClose: () => {
      // Always reset to idle on disconnect — no stuck "Listening…" state.
      setMicState('idle');
    },
  });

  // Lock inputs only while the LLM is actively generating.
  // Once the full response text arrives (llm_text_chunk), inputs unlock so
  // the user can interrupt TTS playback via text or voice.
  const canToggle = !isLlmGenerating;
  const inputDisabled = isLlmGenerating;

  /**
   * Toggle mic on/off.
   * - idle → recording: open mic, notify server.
   * - recording → idle: close mic, notify server.
   * Double-open guard: the idle check prevents re-initialisation if somehow
   * called while already recording.
   */
  const handleToggle = useCallback(async () => {
    if (micState === 'idle') {
      // Guard: check mic permission before transitioning state
      try {
        // Request mic access to surface permission errors early.
        // The actual capture is handled by the Python AudioClient; this call
        // is only used to detect browser-level permission denial.
        await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        console.warn('Microphone access denied: ', err);
        setMicState('idle');
        return;
      }
      setMicState('recording');
      setRoboActive(true);
    } else {
      setMicState('idle');
      setRoboActive(false);
    }
  }, [micState, setRoboActive]);

  // Unmount cleanup: ensure mic is released if the component unmounts while recording.
  useEffect(() => {
    return () => {
      if (micState === 'recording') {
        setRoboActive(false);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [micState]);

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-gray-700 shrink-0">
        <span className="text-xl font-semibold tracking-wide">Voice Assistant</span>

        <div className="flex items-center gap-3">
          {/* Tap to Speak toggle mic button */}
          <TapToSpeakButton
            micState={micState}
            onToggle={handleToggle}
            disabled={!canToggle}
          />

          <StatusIndicator state={pipelineState} />
        </div>
      </header>

      {/* Transcript */}
      <TranscriptDisplay
        messages={messages}
        currentAssistantText={currentAssistantText}
        roboActive={micState === 'recording'}
      />

      {/* Text input */}
      <TextInput onSend={sendTextInput} disabled={inputDisabled} />
    </div>
  );
}
