export type MicState = 'idle' | 'recording';

interface TapToSpeakButtonProps {
  micState: MicState;
  onToggle: () => void;
  disabled?: boolean;
}

/**
 * TapToSpeakButton — replaces the "Activate Robo" button.
 *
 * Pure presentational component: all state is passed via props.
 * Idle state: "Tap to Speak" (gray, mic icon).
 * Recording state: "Listening…" (red, pulsing dot, mic icon).
 */
export default function TapToSpeakButton({
  micState,
  onToggle,
  disabled = false,
}: TapToSpeakButtonProps) {
  const isRecording = micState === 'recording';

  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={isRecording}
      aria-label={isRecording ? 'Stop recording' : 'Start recording'}
      className={`
        flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm
        border transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900
        ${isRecording
          ? 'bg-red-700 border-red-500 text-white hover:bg-red-600 focus:ring-red-500'
          : 'bg-gray-800 border-gray-600 text-gray-300 hover:bg-gray-700 hover:border-gray-500 focus:ring-gray-500'
        }
        disabled:opacity-40 disabled:cursor-not-allowed
      `}
    >
      {/* Mic icon */}
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm-6 9a6 6 0 0 0 12 0h2a8 8 0 0 1-7 7.93V21h2a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2h2v-3.07A8 8 0 0 1 4 10h2z" />
      </svg>

      {isRecording ? 'Listening…' : 'Tap to Speak'}

      {/* Pulsing dot — visible only while recording, mirrors StatusIndicator pattern */}
      {isRecording && (
        <span className="w-2 h-2 rounded-full bg-red-300 animate-pulse" aria-hidden="true" />
      )}
    </button>
  );
}
