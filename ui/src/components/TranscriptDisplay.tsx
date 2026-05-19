import { useEffect, useRef } from 'react';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

interface TranscriptDisplayProps {
  messages: Message[];
  currentAssistantText: string;
  roboActive: boolean;
}

export default function TranscriptDisplay({
  messages,
  currentAssistantText,
  roboActive,
}: TranscriptDisplayProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, currentAssistantText]);

  const hasContent = messages.length > 0 || currentAssistantText.length > 0;

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
      {!hasContent && (
        <p className="text-center text-gray-500 text-lg mt-8">
          {roboActive
            ? <>Robo is listening — <span className="text-violet-400 font-medium">speak your message</span></>
            : <>Press <span className="text-violet-400 font-medium">Activate Robo</span> then speak</>
          }
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

      {currentAssistantText && (
        <div className="flex justify-start">
          <div className="max-w-[75%] text-lg px-4 py-2 rounded-lg leading-relaxed bg-gray-700 text-gray-100">
            {currentAssistantText}
            <span className="inline-block w-0.5 h-5 bg-gray-400 ml-0.5 animate-pulse align-middle" />
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
