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

// ── Simulated streaming helpers ───────────────────────────────────────────
/**
 * Calculate the per-word delay (ms) for a simulated word-by-word render.
 *
 * Target duration scales between 1.5 s (≤15 words) and 6 s (≥60 words).
 * The curve is linear between those two anchors.
 */
function calcWordDelayMs(wordCount: number): number {
  const MIN_DURATION_MS = 1500;  // ≤15 words
  const MAX_DURATION_MS = 6000;  // ≥60 words
  const MIN_WORDS = 15;
  const MAX_WORDS = 60;

  const clampedWords = Math.max(MIN_WORDS, Math.min(MAX_WORDS, wordCount));
  const t = (clampedWords - MIN_WORDS) / (MAX_WORDS - MIN_WORDS); // 0..1
  const totalMs = MIN_DURATION_MS + t * (MAX_DURATION_MS - MIN_DURATION_MS);
  return totalMs / Math.max(wordCount, 1);
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

  // ── Simulated word-by-word render state ──────────────────────────────────
  // True while the simulated render is in progress — keeps inputs locked.
  const isSimRenderingRef = useRef(false);
  // setTimeout handle for the current word step.
  const simRenderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The pipeline state received from the server while sim-rendering (applied after render).
  const pendingPipelineStateRef = useRef<'listening' | 'thinking' | 'speaking' | null>(null);

  /**
   * Cancel any in-progress simulated render immediately and flush all
   * remaining words to the display at once.
   */
  const cancelSimRender = useCallback(() => {
    if (simRenderTimerRef.current !== null) {
      clearTimeout(simRenderTimerRef.current);
      simRenderTimerRef.current = null;
    }
    isSimRenderingRef.current = false;
  }, []);

  /**
   * Start a simulated word-by-word render of `text`.
   * Words are revealed one at a time with `delayMs` between each.
   * When the last word is shown, `onComplete` is called.
   */
  const startSimRender = useCallback(
    (text: string, onComplete: () => void) => {
      // Cancel any previous render that might still be running.
      cancelSimRender();

      const words = text.split(' ');
      const delayMs = calcWordDelayMs(words.length);
      let wordIndex = 0;
      isSimRenderingRef.current = true;

      // Reset display to empty before starting.
      setCurrentAssistantText('');

      const step = () => {
        if (!isMountedRef.current || !isSimRenderingRef.current) return;

        wordIndex += 1;
        // Reconstruct the visible text up to the current word index.
        const visible = words.slice(0, wordIndex).join(' ');
        setCurrentAssistantText(visible);

        if (wordIndex < words.length) {
          simRenderTimerRef.current = setTimeout(step, delayMs);
        } else {
          // All words shown — render complete.
          simRenderTimerRef.current = null;
          isSimRenderingRef.current = false;
          onComplete();
        }
      };

      simRenderTimerRef.current = setTimeout(step, delayMs);
    },
    [cancelSimRender],
  );

  // ── Legacy token render queue (kept for any future streaming use) ─────────
  // NOTE: with simulated rendering the token queue is no longer used for
  // llm_text_chunk, but we keep flushTokenQueue for the transcript/status
  // handlers that relied on it to commit in-progress text to messages.
  const tokenQueueRef = useRef<string[]>([]);
  const rafIdRef = useRef<number | null>(null);

  const drainTokenQueue = useCallback(() => {
    rafIdRef.current = null;
    if (!isMountedRef.current) return;
    const queue = tokenQueueRef.current;
    if (queue.length === 0) return;
    const tokens = queue.splice(0, queue.length > 30 ? 2 : 1);
    setCurrentAssistantText((prev) => prev + tokens.join(''));
    if (queue.length > 0) {
      rafIdRef.current = requestAnimationFrame(drainTokenQueue);
    }
  }, []);

  // Flush: cancel any pending RAF drain and commit remaining tokens instantly.
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
          // A new user transcript means a new turn is starting.
          // Cancel any in-progress simulated render and commit whatever was shown.
          cancelSimRender();
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
          // Full response text arrives as a single chunk.
          // Render it word-by-word with a calculated delay.
          // Keep pipelineState as 'speaking' for the entire render duration
          // so inputs remain locked until the last word is shown.
          const responseText = (data.text as string).trim();
          if (!responseText) break;

          // Ensure inputs are locked while rendering.
          setPipelineState('speaking');

          startSimRender(responseText, () => {
            // Render complete — apply any pipeline state update that arrived
            // while we were rendering (e.g. 'listening' from the server).
            if (!isMountedRef.current) return;
            const pending = pendingPipelineStateRef.current;
            pendingPipelineStateRef.current = null;
            if (pending) {
              setPipelineState(pending);
              if (pending === 'listening') {
                // Commit the fully-rendered text to the message history.
                const finalText = currentAssistantTextRef.current;
                if (finalText) {
                  const id = crypto.randomUUID();
                  setMessages((prev) => [...prev, { id, role: 'assistant', text: finalText }]);
                  setCurrentAssistantText('');
                }
              }
            }
          });
          break;
        }

        case 'status': {
          const state = data.state as string;
          if (state === 'listening' || state === 'thinking' || state === 'speaking') {
            if (isSimRenderingRef.current) {
              // A simulated render is in progress — defer the state update
              // until the render completes so inputs stay locked.
              pendingPipelineStateRef.current = state as 'listening' | 'thinking' | 'speaking';
              if (state === 'listening') {
                // Don't flush/commit yet — the render onComplete handler will do it.
              }
            } else {
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
  }, [flushTokenQueue, startSimRender, cancelSimRender]);

  useEffect(() => {
    isMountedRef.current = true;
    connect();
    return () => {
      isMountedRef.current = false;
      cancelSimRender();
      if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current);
      if (reconnectTimerRef.current !== null) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect, cancelSimRender]);

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
