interface StatusIndicatorProps {
  state: 'listening' | 'thinking' | 'speaking' | 'disconnected';
}

export default function StatusIndicator({ state }: StatusIndicatorProps) {
  return (
    <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 border border-gray-700">
      {state === 'listening' && (
        <>
          <span className="w-3 h-3 rounded-full bg-green-500 animate-pulse" />
          <span className="text-sm font-semibold tracking-widest text-green-400">LISTENING</span>
        </>
      )}
      {state === 'thinking' && (
        <>
          <span className="w-4 h-4 rounded-full border-2 border-amber-500 border-t-transparent animate-spin" aria-hidden="true" />
          <span className="text-sm font-semibold tracking-widest text-amber-400">THINKING</span>
        </>
      )}
      {state === 'speaking' && (
        <>
          <span className="flex items-end gap-0.5 h-4" aria-hidden="true">
            <span className="w-1 h-2 bg-blue-500 rounded-sm animate-bounce [animation-delay:0ms]" />
            <span className="w-1 h-4 bg-blue-500 rounded-sm animate-bounce [animation-delay:150ms]" />
            <span className="w-1 h-2 bg-blue-500 rounded-sm animate-bounce [animation-delay:300ms]" />
          </span>
          <span className="text-sm font-semibold tracking-widest text-blue-400">SPEAKING</span>
        </>
      )}
      {state === 'disconnected' && (
        <>
          <span className="w-3 h-3 rounded-full bg-gray-500" />
          <span className="text-sm font-semibold tracking-widest text-gray-400">DISCONNECTED</span>
        </>
      )}
    </div>
  );
}
