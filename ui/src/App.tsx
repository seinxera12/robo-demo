import StatusIndicator from './components/StatusIndicator';
import TranscriptDisplay from './components/TranscriptDisplay';
import TextInput from './components/TextInput';
import { useVoiceWebSocket } from './hooks/useVoiceWebSocket';

export default function App() {
  const { pipelineState, messages, currentAssistantText, sendTextInput } =
    useVoiceWebSocket();

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-white">
      {/* Header bar */}
      <header className="flex items-center justify-center gap-3 px-6 py-4 border-b border-gray-700 shrink-0">
        <span className="text-xl font-semibold tracking-wide">Voice Assistant</span>
        <StatusIndicator state={pipelineState} />
      </header>

      {/* Transcript — fills remaining space and scrolls */}
      <TranscriptDisplay
        messages={messages}
        currentAssistantText={currentAssistantText}
      />

      {/* Text input pinned to bottom */}
      <TextInput onSend={sendTextInput} />
    </div>
  );
}
