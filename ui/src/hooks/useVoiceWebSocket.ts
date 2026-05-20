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
  /** True only while the LLM is actively generating (thinking state).
   *  False as soon as the full response text arrives, even if TTS is still playing.
   *  Use this — not pipelineState — to decide whether to lock inputs. */
  isLlmGenerating: boolean;
  sendTextInput: (text: string) => void;
  setRoboActive: (active: boolean) => void;
}

// ── Simulated streaming helpers ───────────────────────────────────────────

/**
 * Returns true if the text is predominantly CJK (Chinese/Japanese/Korean).
 * We check whether the majority of non-whitespace characters fall in CJK
 * Unicode ranges so that mixed-language responses are handled gracefully.
 */
function isCJKText(text: string): boolean {
  // CJK Unified Ideographs, Hiragana, Katakana, CJK Compatibility, etc.
  const cjkPattern = /[\u3000-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]/g;
  const nonWhitespace = text.replace(/\s/g, '');
  if (nonWhitespace.length === 0) return false;
  const cjkMatches = nonWhitespace.match(cjkPattern);
  const cjkCount = cjkMatches ? cjkMatches.length : 0;
  return cjkCount / nonWhitespace.length > 0.3;
}

/**
 * Split text into render tokens.
 *
 * - For CJK text: each individual character is a token (no spaces between
 *   characters in Japanese/Chinese, so space-splitting produces one giant token).
 * - For Latin/other text: split on whitespace as before, preserving words.
 *
 * Returns { tokens, joinWith } where joinWith is the string used to
 * reconstruct the visible text from tokens[0..n].
 */
function tokenizeText(text: string): { tokens: string[]; joinWith: string } {
  if (isCJKText(text)) {
    // Split into individual characters, filtering empty strings.
    const tokens = Array.from(text).filter((ch) => ch.length > 0);
    return { tokens, joinWith: '' };
  }
  // Default: split on spaces (original behaviour).
  return { tokens: text.split(' '), joinWith: ' ' };
}

/**
 * Calculate the per-token delay (ms) for a simulated word-by-word render.
 *
 * Target duration scales between 1.5 s (≤15 tokens) and 6 s (≥60 tokens).
 * The curve is linear between those two anchors.
 *
 * For CJK text the "token" is a single character, so the anchor counts are
 * scaled up (×4) to keep the same wall-clock feel — a 60-character Japanese
 * response should still complete in ~5–6 s.
 */
function calcTokenDelayMs(tokenCount: number, isCJK: boolean): number {
  const MIN_DURATION_MS = 1500;
  const MAX_DURATION_MS = 6000;
  // CJK characters are much more numerous than space-delimited words for the
  // same amount of content, so scale the anchor thresholds accordingly.
  const MIN_TOKENS = isCJK ? 60  : 15;
  const MAX_TOKENS = isCJK ? 240 : 60;

  const clampedTokens = Math.max(MIN_TOKENS, Math.min(MAX_TOKENS, tokenCount));
  const t = (clampedTokens - MIN_TOKENS) / (MAX_TOKENS - MIN_TOKENS); // 0..1
  const totalMs = MIN_DURATION_MS + t * (MAX_DURATION_MS - MIN_DURATION_MS);
  return totalMs / Math.max(tokenCount, 1);
}

export function useVoiceWebSocket(options?: UseVoiceWebSocketOptions): UseVoiceWebSocketReturn {
  const [pipelineState, setPipelineState] = useState<
    'listening' | 'thinking' | 'speaking' | 'disconnected'
  >('disconnected');
  const [roboActive, setRoboActiveState] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentAssistantText, setCurrentAssistantText] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  // True only while the LLM is generating (server state = 'thinking').
  // Set to false as soon as llm_text_chunk arrives — inputs unlock at that point
  // regardless of TTS synthesis / playback state.
  const [isLlmGenerating, setIsLlmGenerating] = useState(false);

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
   * Start a simulated token-by-token render of `text`.
   * For Latin text, tokens are space-delimited words (original behaviour).
   * For CJK text (Japanese, Chinese, Korean), tokens are individual characters
   * so that the animation works correctly without spaces.
   * When the last token is shown, `onComplete` is called.
   */
  const startSimRender = useCallback(
    (text: string, onComplete: () => void) => {
      // Cancel any previous render that might still be running.
      cancelSimRender();

      const cjk = isCJKText(text);
      const { tokens, joinWith } = tokenizeText(text);
      const delayMs = calcTokenDelayMs(tokens.length, cjk);
      let tokenIndex = 0;
      isSimRenderingRef.current = true;

      // Reset display to empty before starting.
      setCurrentAssistantText('');

      const step = () => {
        if (!isMountedRef.current || !isSimRenderingRef.current) return;

        tokenIndex += 1;
        // Reconstruct the visible text up to the current token index.
        const visible = tokens.slice(0, tokenIndex).join(joinWith);
        setCurrentAssistantText(visible);

        if (tokenIndex < tokens.length) {
          simRenderTimerRef.current = setTimeout(step, delayMs);
        } else {
          // All tokens shown — render complete.
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
          // Full response text arrives as a single chunk — LLM generation is done.
          // Unlock inputs immediately so the user can interrupt TTS playback.
          setIsLlmGenerating(false);

          const responseText = (data.text as string).trim();
          if (!responseText) break;

          // Ensure the status indicator shows 'speaking' while TTS plays,
          // but do NOT lock inputs (isLlmGenerating is already false).
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
            // Track LLM generation state: lock on 'thinking', unlock on anything else.
            // (llm_text_chunk also unlocks — this handles the case where 'listening'
            // arrives before llm_text_chunk, e.g. after an interrupt.)
            if (state === 'thinking') {
              setIsLlmGenerating(true);
            } else {
              setIsLlmGenerating(false);
            }

            if (isSimRenderingRef.current) {
              if (state === 'listening') {
                // Server transitioned to listening while we were rendering —
                // this means an interrupt happened.  Flush the render immediately,
                // commit whatever text was shown, and apply the state now.
                cancelSimRender();
                pendingPipelineStateRef.current = null;
                const interruptedText = currentAssistantTextRef.current;
                if (interruptedText) {
                  const id = crypto.randomUUID();
                  setMessages((prev) => [...prev, { id, role: 'assistant', text: interruptedText }]);
                  setCurrentAssistantText('');
                }
                setPipelineState('listening');
              } else {
                // A simulated render is in progress — defer non-interrupt state updates
                // until the render completes so the status indicator stays in sync.
                pendingPipelineStateRef.current = state as 'listening' | 'thinking' | 'speaking';
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
      setIsLlmGenerating(false);
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
    isLlmGenerating,
    sendTextInput,
    setRoboActive,
  };
}
