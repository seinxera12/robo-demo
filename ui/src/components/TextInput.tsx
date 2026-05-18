import { KeyboardEvent, useRef, useState } from 'react';

interface TextInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export default function TextInput({ onSend, disabled = false }: TextInputProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue('');
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex gap-2 px-4 py-3 border-t border-gray-700 bg-gray-900">
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder="Type a message instead…"
        className="flex-1 bg-gray-800 text-white text-lg placeholder-gray-500 rounded-lg px-4 py-2 border border-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
        aria-label="Type a message"
      />
      <button
        onClick={handleSubmit}
        disabled={disabled || !value.trim()}
        className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white text-lg font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-label="Send message"
      >
        Send
      </button>
    </div>
  );
}
