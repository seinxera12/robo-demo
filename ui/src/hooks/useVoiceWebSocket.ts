import { useCallback, useEffect, useRef, useState } from 'react';

const WS_URL = 'ws://localhost:8000/ws/ui';

const RECONNECT_DELAY_MS = 2000;

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

interface UseVoiceWebSocketReturn {
  pipelineState: 'listening' | 'thinking' | 'speaking' | 'disconnected';
  messages: Message[];
  currentAssistantText: string;
  sessionId: string | null;
  sendTextInput: (text: string) => void;
}

export function useVoiceWebSocket(): UseVoiceWebSocketReturn {
  const [pipelineState, setPipelineState] = useState<
    'listening' | 'thinking' | 'speaking' | 'disconnected'
  >('disconnected');
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentAssistantText, setCurrentAssistantText] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(true);

  // Keep a ref to currentAssistantText so event handlers always see the latest value
  const currentAssistantTextRef = useRef('');
  currentAssistantTextRef.current = currentAssistantText;

  const finalizeAssistantMessage = useCallback(() => {
    const text = currentAssistantTextRef.current;
    if (!text) return;
    const id = crypto.randomUUID();
    setMessages((prev) => [...prev, { id, role: 'assistant', text }]);
    setCurrentAssistantText('');
  }, []);

  const connect = useCallback(() => {
    if (!isMountedRef.current) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!isMountedRef.current) {
        ws.close();
        return;
      }
      // Clear any pending reconnect timer
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    ws.onmessage = (event: MessageEvent) => {
      if (!isMountedRef.current) return;

      let data: Record<string, unknown>;
      try {
        data = JSON.parse(event.data as string) as Record<string, unknown>;
      } catch {
        return;
      }

      const type = data.type as string;

      switch (type) {
        case 'session_start': {
          setSessionId(data.session_id as string);
          break;
        }

        case 'transcript': {
          // Finalize any pending assistant text before adding the user message
          if (currentAssistantTextRef.current) {
            const assistantId = crypto.randomUUID();
            const assistantText = currentAssistantTextRef.current;
            setMessages((prev) => [
              ...prev,
              { id: assistantId, role: 'assistant', text: assistantText },
            ]);
            setCurrentAssistantText('');
          }
          const userId = crypto.randomUUID();
          setMessages((prev) => [
            ...prev,
            { id: userId, role: 'user', text: data.text as string },
          ]);
          break;
        }

        case 'llm_text_chunk': {
          setCurrentAssistantText((prev) => prev + (data.text as string));
          break;
        }

        case 'status': {
          const state = data.state as string;
          if (
            state === 'listening' ||
            state === 'thinking' ||
            state === 'speaking'
          ) {
            setPipelineState(state);
            // Finalize assistant message when transitioning back to listening
            if (state === 'listening' && currentAssistantTextRef.current) {
              const assistantId = crypto.randomUUID();
              const assistantText = currentAssistantTextRef.current;
              setMessages((prev) => [
                ...prev,
                { id: assistantId, role: 'assistant', text: assistantText },
              ]);
              setCurrentAssistantText('');
            }
          }
          break;
        }

        default:
          break;
      }
    };

    ws.onclose = () => {
      if (!isMountedRef.current) return;
      setPipelineState('disconnected');
      wsRef.current = null;
      // Schedule reconnect
      reconnectTimerRef.current = setTimeout(() => {
        if (isMountedRef.current) {
          connect();
        }
      }, RECONNECT_DELAY_MS);
    };

    ws.onerror = () => {
      // onclose will fire after onerror, so reconnect is handled there
      ws.close();
    };
  }, [finalizeAssistantMessage]);

  useEffect(() => {
    isMountedRef.current = true;
    connect();

    return () => {
      isMountedRef.current = false;
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  const sendTextInput = useCallback((text: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'text_input', text }));
    }
  }, []);

  return {
    pipelineState,
    messages,
    currentAssistantText,
    sessionId,
    sendTextInput,
  };
}
