import { useEffect, useRef } from 'react';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

interface TranscriptDisplayProps {
  messages: Message[];
  currentAssistantText: string;
}

export default function TranscriptDisplay({
  messages,
  currentAssistantText,
}: TranscriptDisplayProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the latest message whenever messages or streaming text changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentAssistantText]);

  const hasContent = messages.length > 0 || currentAssistantText.length > 0;

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
      {!hasContent && (
        <p className="text-center text-gray-500 text-lg mt-8">
          Start speaking or type a message to begin…
        </p>
      )}

      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
        >
          <div
            className={`max-w-[75%] text-lg px-4 py-2 rounded-lg leading-relaxed ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-100'
            }`}
          >
            {msg.text}
          </div>
        </div>
      ))}

      {/* Streaming assistant text — appended character-by-character as llm_text_chunk messages arrive */}
      {currentAssistantText && (
        <div className="flex justify-start">
          <div className="max-w-[75%] text-lg px-4 py-2 rounded-lg leading-relaxed bg-gray-700 text-gray-100">
            {currentAssistantText}
            {/* Blinking cursor to indicate active streaming */}
            <span className="inline-block w-0.5 h-5 bg-gray-400 ml-0.5 animate-pulse align-middle" />
          </div>
        </div>
      )}

      {/* Sentinel element for auto-scroll */}
      <div ref={bottomRef} />
    </div>
  );
}
