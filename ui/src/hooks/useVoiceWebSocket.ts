import { useCallback, useEffect, useRef, useState } from 'react';

const WS_URL = 'ws://localhost:8000/ws/ui';
const RECONNECT_DELAY_MS = 2000;

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

interface UseVoiceWebSocketOptions {
  /** Called when the server emits `robo_deactivated` (end-of-turn / VAD silence). */
  onRoboDeactivated?: () => void;
  /** Called when the WebSocket connection closes. */
  onWsClose?: () => void;
}

interface UseVoiceWebSocketReturn {
  pipelineState: 'listening' | 'thinking' | 'speaking' | 'disconnected';
  roboActive: boolean;
  messages: Message[];
  currentAssistantText: string;
  sessionId: string | null;
  sendTextInput: (text: string) => void;
  setRoboActive: (active: boolean) => void;
}

export function useVoiceWebSocket(options?: UseVoiceWebSocketOptions): UseVoiceWebSocketReturn {
  const [pipelineState, setPipelineState] = useState<
    'listening' | 'thinking' | 'speaking' | 'disconnected'
  >('disconnected');
  const [roboActive, setRoboActiveState] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentAssistantText, setCurrentAssistantText] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(true);
  // Keep options in a ref so the stable `connect` callback always sees the latest values
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const currentAssistantTextRef = useRef('');
  currentAssistantTextRef.current = currentAssistantText;

  // ── Token render queue ────────────────────────────────────────────────────
  // Tokens arrive faster than React can paint. Queue them and drain one per
  // animation frame so each token gets its own render — word-by-word effect
  // with zero artificial delay added to the model.
  const tokenQueueRef = useRef<string[]>([]);
  const rafIdRef = useRef<number | null>(null);

  const drainTokenQueue = useCallback(() => {
    rafIdRef.current = null;
    if (!isMountedRef.current) return;
    const queue = tokenQueueRef.current;
    if (queue.length === 0) return;
    // Drain 1 token per frame; catch up with 2 if queue is growing
    const tokens = queue.splice(0, queue.length > 30 ? 2 : 1);
    setCurrentAssistantText((prev) => prev + tokens.join(''));
    if (queue.length > 0) {
      rafIdRef.current = requestAnimationFrame(drainTokenQueue);
    }
  }, []);

  const scheduleTokenDrain = useCallback(() => {
    if (rafIdRef.current === null) {
      rafIdRef.current = requestAnimationFrame(drainTokenQueue);
    }
  }, [drainTokenQueue]);

  const flushTokenQueue = useCallback(() => {
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    const remaining = tokenQueueRef.current.splice(0).join('');
    if (remaining) setCurrentAssistantText((prev) => prev + remaining);
  }, []);

  // ── Robo active toggle — sends set_active to server ──────────────────────
  const setRoboActive = useCallback((active: boolean) => {
    setRoboActiveState(active);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'set_active', active }));
    }
  }, []);

  // ── WebSocket connection ──────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!isMountedRef.current) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!isMountedRef.current) { ws.close(); return; }
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
      } catch { return; }

      const type = data.type as string;

      switch (type) {
        case 'session_start': {
          setSessionId(data.session_id as string);
          break;
        }

        case 'robo_deactivated': {
          // Server signals that a turn completed — reset the button
          setRoboActiveState(false);
          optionsRef.current?.onRoboDeactivated?.();
          break;
        }

        case 'transcript': {
          flushTokenQueue();
          if (currentAssistantTextRef.current) {
            const id = crypto.randomUUID();
            const text = currentAssistantTextRef.current;
            setMessages((prev) => [...prev, { id, role: 'assistant', text }]);
            setCurrentAssistantText('');
          }
          setMessages((prev) => [
            ...prev,
            { id: crypto.randomUUID(), role: 'user', text: data.text as string },
          ]);
          break;
        }

        case 'llm_text_chunk': {
          tokenQueueRef.current.push(data.text as string);
          scheduleTokenDrain();
          break;
        }

        case 'status': {
          const state = data.state as string;
          if (state === 'listening' || state === 'thinking' || state === 'speaking') {
            setPipelineState(state);
            if (state === 'listening') {
              flushTokenQueue();
              setTimeout(() => {
                if (!currentAssistantTextRef.current) return;
                const id = crypto.randomUUID();
                const text = currentAssistantTextRef.current;
                setMessages((prev) => [...prev, { id, role: 'assistant', text }]);
                setCurrentAssistantText('');
              }, 0);
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
      setRoboActiveState(false);
      optionsRef.current?.onWsClose?.();
      wsRef.current = null;
      reconnectTimerRef.current = setTimeout(() => {
        if (isMountedRef.current) connect();
      }, RECONNECT_DELAY_MS);
    };

    ws.onerror = () => { ws.close(); };
  }, [flushTokenQueue, scheduleTokenDrain]);

  useEffect(() => {
    isMountedRef.current = true;
    connect();
    return () => {
      isMountedRef.current = false;
      if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current);
      if (reconnectTimerRef.current !== null) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
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
    roboActive,
    messages,
    currentAssistantText,
    sessionId,
    sendTextInput,
    setRoboActive,
  };
}
