import StatusIndicator from './components/StatusIndicator';
import TranscriptDisplay from './components/TranscriptDisplay';
import TextInput from './components/TextInput';
import { useVoiceWebSocket } from './hooks/useVoiceWebSocket';

export default function App() {
  const {
    pipelineState,
    roboActive,
    messages,
    currentAssistantText,
    sendTextInput,
    setRoboActive,
  } = useVoiceWebSocket();

  // Only block the button while a turn is actively in progress.
  // Disconnected means the UI WS isn't connected yet — that's handled
  // separately; don't block the button for it.
  const canToggle = pipelineState !== 'thinking' && pipelineState !== 'speaking';

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-gray-700 shrink-0">
        <span className="text-xl font-semibold tracking-wide">Voice Assistant</span>

        <div className="flex items-center gap-3">
          {/* Robo activate / deactivate button */}
          <button
            onClick={() => setRoboActive(!roboActive)}
            disabled={!canToggle}
            aria-pressed={roboActive}
            aria-label={roboActive ? 'Deactivate Robo' : 'Activate Robo'}
            className={`
              flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm
              border transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900
              ${roboActive
                ? 'bg-violet-700 border-violet-500 text-white hover:bg-violet-600 focus:ring-violet-500'
                : 'bg-gray-800 border-gray-600 text-gray-300 hover:bg-gray-700 hover:border-gray-500 focus:ring-gray-500'
              }
              disabled:opacity-40 disabled:cursor-not-allowed
            `}
          >
            {/* Robot icon */}
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 2a2 2 0 0 1 2 2v1h3a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3h3V4a2 2 0 0 1 2-2zm-5 6a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1H7zm2 3a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm6 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm-5 4h4a.5.5 0 0 1 0 1h-4a.5.5 0 0 1 0-1z" />
            </svg>
            {roboActive ? 'Robo Active' : 'Activate Robo'}
            {roboActive && (
              <span className="w-2 h-2 rounded-full bg-violet-300 animate-pulse" aria-hidden="true" />
            )}
          </button>

          <StatusIndicator state={pipelineState} />
        </div>
      </header>

      {/* Transcript */}
      <TranscriptDisplay
        messages={messages}
        currentAssistantText={currentAssistantText}
        roboActive={roboActive}
      />

      {/* Text input */}
      <TextInput onSend={sendTextInput} />
    </div>
  );
}
