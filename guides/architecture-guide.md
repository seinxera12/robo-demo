--- 

# Robo — Architecture Discovery Report

## Directory layout

```
demo/
├── .env                        # Runtime secrets (GROQ_API_KEY, etc.)
├── .env.example                # Template with all configurable keys
├── pyproject.toml              # Python project metadata + uv lockfile source
├── requirements.txt            # Pinned Python dependencies
├── uv.lock                     # Deterministic dependency lock (uv)
├── start.bat                   # Windows launcher (server + client + browser)
├── start.sh                    # Linux/macOS launcher
├── builder.log                 # PyInstaller build log (last successful run)
│
├── server/                     # FastAPI backend
│   ├── main.py                 # FastAPI app, lifespan, WebSocket endpoints
│   ├── pipeline.py             # 5-worker asyncio voice pipeline (PipelineState, VoicePipeline)
│   ├── config.py               # Config dataclass — loads .env
│   ├── models.py               # Data models (TranscriptionResult, WS message TypedDicts)
│   ├── log.py                  # Structured logging (system.log + 4 detail logs)
│   ├── lang/
│   │   └── detector.py         # Language mapper (Groq Whisper lang → "en"/"ja")
│   ├── llm/
│   │   ├── assembler.py        # DeploymentConfig, PromptAssembler, detect_model_tier
│   │   ├── chain.py            # LLMChain (Groq primary, Gemini fallback)
│   │   ├── groq_llm.py         # Groq streaming LLaMA backend
│   │   ├── gemini_llm.py       # Gemini streaming fallback backend
│   │   ├── intent.py           # LLM-based intent classifier (Call 1)
│   │   ├── router.py           # Route selector + RAG/search retrieval
│   │   ├── postprocess.py      # Markdown/whitespace stripper for TTS
│   │   └── prompt_builder.py   # Deprecated; superseded by assembler.py
│   ├── stt/
│   │   └── groq_stt.py         # Groq Whisper STT (PCM16 → WAV → transcription)
│   ├── tts/
│   │   ├── kokoro_tts.py       # Local Kokoro EN + JA TTS engines
│   │   └── tts_router.py       # Language router + sentence boundary accumulator
│   ├── search/
│   │   └── tavily_search.py    # Tavily web search client
│   └── prompts/                # Prompt template text files
│       ├── base.txt            # Core Robo persona (groq-tier)
│       ├── base_small.txt      # Core Robo persona (small-tier)
│       ├── classifier.txt      # Intent classifier prompt (groq-tier)
│       ├── classifier_small.txt# Intent classifier prompt (small-tier)
│       ├── lang_en.txt         # English language/tone block
│       ├── lang_ja.txt         # Japanese language/tone block
│       └── lang_unknown.txt    # Fallback language block
│
├── ui/                         # React SPA (TypeScript + Vite + Tailwind)
│   ├── src/
│   │   ├── App.tsx             # Root component, mic state machine, layout
│   │   ├── main.tsx            # React entry point
│   │   ├── hooks/
│   │   │   └── useVoiceWebSocket.ts  # WS connection, message dispatch, sim-render
│   │   └── components/
│   │       ├── TapToSpeakButton.tsx  # Tap-to-toggle mic button
│   │       ├── TranscriptDisplay.tsx # Scrolling chat bubble view
│   │       ├── TextInput.tsx         # Text fallback input
│   │       └── StatusIndicator.tsx   # Pipeline state badge (LISTENING/THINKING/SPEAKING)
│   ├── dist/                   # Built SPA assets (served by FastAPI)
│   ├── package.json
│   └── vite.config.ts
│
├── client/                     # Python AudioClient process
│   ├── main.py                 # Entry point — wires all components
│   ├── audio_capture.py        # sounddevice InputStream (16 kHz PCM16, 32ms frames)
│   ├── vad.py                  # Silero VAD (speech_start / speech_end / barge-in)
│   ├── ws_client.py            # WebSocket client → /ws endpoint
│   └── audio_playback.py       # sounddevice playback of 24 kHz WAV chunks
│
├── config/
│   └── deployment.yaml         # Per-deployment identity, language, RAG docs, OOS
│
├── models/
│   └── silero_vad.jit          # Pre-downloaded Silero VAD TorchScript model
│
├── logging/                    # Rolling log files (rotated on start)
│   ├── system.log  llm.log  stt.log  tts.log  search.log
│
├── installer/                  # NSIS/VC installer assets
│   └── vc_redist.x64.exe
│
├── hooks/                      # PyInstaller runtime hooks (rthook_paths.py, rthook_transformers.py)
│                               # (source deleted after last build; only .pyc in __pycache__)
├── dist/DemoVoiceAssistant/    # Packaged Windows distribution
└── build/DemoVoiceAssistant/   # PyInstaller intermediate build artefacts
```

---

## Frontend summary

|Property|Detail|
|---|---|
|Framework|React 18.3 + TypeScript 5.6|
|Build tool|Vite 5.4 (`tsc && vite build`)|
|Styling|Tailwind CSS 3.4 + PostCSS|
|Testing|Vitest 4.1 + Testing Library + fast-check (property tests)|
|No router|Single-page app; no client-side routing (single `App.tsx`)|
|Entry point|`ui/src/main.tsx` → `<App />` mounted at `#root` in `ui/index.html`|

**How the built dist is served**: FastAPI mounts `ui/dist/` as a `StaticFiles` directory at the HTTP root (`/`) with `html=True`. The React SPA is therefore served directly by uvicorn — there is no separate static server.

**Audio capture method**: Audio capture does **not** happen in the browser. The Python `AudioCapture` process (`client/audio_capture.py`) opens a `sounddevice.InputStream` at 16 kHz, 1 channel, int16, with 32ms frames (512 samples). The browser UI does call `navigator.mediaDevices.getUserMedia` once as a permission check, but immediately discards the stream — it does not actually capture audio.

**Playback method**: Playback is also handled in Python (`client/audio_playback.py`) using `sounddevice.play()` (blocking, float32, 24 kHz). The browser has no audio playback capability.

**WebSocket vs REST**: The UI uses **WebSocket exclusively** — no REST calls at all. It connects to `ws://localhost:8000/ws/ui` and communicates through JSON messages only (`text_input`, `set_active`, `transcript`, `status`, `llm_text_chunk`, `robo_deactivated`, `session_start`).

**Simulated streaming render**: The `llm_text_chunk` message carries the full completed response as a single string. The UI fakes a streaming appearance by tokenizing the text (space-split for Latin, character-split for CJK) and rendering tokens with a calculated per-token delay (1.5 s for 15 tokens → 6 s for 60 tokens, scaled ×4 for CJK).

---

## Backend API surface

The server exposes **no HTTP REST endpoints**. All communication is over WebSocket.

|Protocol|Path|Connected by|Direction|Purpose|
|---|---|---|---|---|
|WebSocket|`/ws`|Python `client/` process|bidirectional|Binary PCM16 audio in; binary WAV out; JSON status/interrupt control|
|WebSocket|`/ws/ui`|Browser React SPA|bidirectional|JSON-only; receives pipeline events; sends `text_input` and `set_active`|
|HTTP GET|`/*`|Browser|server → client|Static file serving of React SPA from `ui/dist/` (StaticFiles mount)|

**`/ws` message schema:**

- Inbound binary: raw PCM16 bytes (audio frames from mic)
- Inbound binary (JSON-wrapped): `{"type": "interrupt"}` — barge-in signal
- Inbound text JSON: `{"type": "interrupt"}` — barge-in via text channel
- Outbound binary: WAV bytes (24 kHz synthesised TTS audio)
- Outbound text JSON: `{"type": "status", "state": "listening"|"thinking"|"speaking"}`

**`/ws/ui` message schema:**

- Inbound: `{"type": "text_input", "text": "..."}` | `{"type": "set_active", "active": bool}`
- Outbound: `{"type": "session_start", "session_id": "..."}` | `{"type": "status", "state": "..."}` | `{"type": "transcript", "text": "...", "language": "..."}` | `{"type": "llm_text_chunk", "text": "..."}` | `{"type": "robo_deactivated"}`

**Lifespan events**: On startup, the `lifespan` context manager initialises all components, runs concurrent `warm_up()` calls on both Kokoro TTS engines, and sends a Groq API connectivity ping. On shutdown, it cancels all active pipeline tasks.

**CORS/auth/session middleware**: None. No CORS, no auth, no session middleware is present in the source. The server is designed for trusted local network use.

---

## Voice and AI pipeline

### Full per-turn flow

```
[Microphone]
    │ 32ms PCM16 frames (16 kHz, 512 samples)
    ▼
AudioCapture (sounddevice InputStream)
    │
    ▼
SileroVAD (Silero VAD TorchScript, local, models/silero_vad.jit loaded via torch.hub)
    │  speech_end → accumulated PCM16 buffer
    │  barge_in  → AudioPlayback.interrupt() + WSClient.send_interrupt()
    ▼
WSClient.send_audio(pcm16_bytes)  — binary frame over /ws WebSocket
    │
    ▼  [Server /ws]
audio_input_worker (Worker 1)
    │ puts pcm16 bytes into audio_queue
    │ also triggers interrupt_controller.request_interrupt() on new audio during thinking/speaking
    ▼
stt_worker (Worker 2)
    │ GATE: drops audio when robo_active == False
    │ wraps PCM16 in WAV header (wave module, in-memory)
    │ calls Groq Whisper API (whisper-large-v3-turbo, EXTERNAL, async)
    │   response_format="verbose_json" → text + language ISO code
    │ LanguageDetector maps ISO code → "en" | "ja"
    │ broadcasts {"type": "transcript"} to /ws/ui clients
    ▼
transcript_queue → llm_worker (Worker 3)
    │
    ├─ [Call 1 — Intent Classification] (EXTERNAL, Groq LLaMA)
    │   messages = [classifier.txt system prompt] + [user transcript]
    │   max_tokens=150, temperature=0.0
    │   returns IntentResult: intent, language, confidence, needs_clarification, query_clean
    │
    ├─ [Router] — decides route:
    │   • "general"      → no context retrieval
    │   • "environment"  → reads environment_docs files (local file RAG, Phase 1: raw text concat)
    │   • "web_search"   → TavilySearchClient.search(query_clean) (EXTERNAL, Tavily API)
    │   • "out_of_scope" → direct_response from deployment.yaml config, skips Call 2
    │   • "clarify"      → direct clarification response if conf < 0.3; suffix if 0.3–0.45
    │
    ├─ [PromptAssembler] — builds messages array:
    │   blocks: base.txt → deployment block (rendered from DeploymentConfig) → lang_*.txt → route context block
    │   trims history to session_memory_turns pairs / 600 token word-count cap
    │
    └─ [Call 2 — LLM Generation] (streaming, EXTERNAL)
        primary:  Groq LLaMA (llama-3.3-70b-versatile) via GroqLLMBackend
        fallback: Gemini (gemini-2.0-flash) via GeminiLLMBackend (if GEMINI_API_KEY set)
        max_tokens=200, temperature=0.65
        tokens pushed to token_queue one-by-one as they arrive
        interrupt_controller.cancelled checked each token — stream aborted if interrupted
    │
    ▼
token_queue → tts_worker (Worker 4)
    │ TTSRouter.accumulate(token) — sentence boundary detection:
    │   hard boundaries: . ? ! 。？！
    │   soft: comma when buffer ≥ 60 chars
    │   hard cap: flush at 120 chars regardless
    │ on sentence boundary → TTSRouter.synthesize(sentence, language)
    │   language routing: if CJK chars detected → KokoroJapaneseTTS (lang_code='j', voice=jf_alpha)
    │                      else                 → KokoroTTS (lang_code='a', voice=af_heart)
    │   synthesis runs in thread executor (asyncio.run_in_executor) — LOCAL, CPU
    │   output: 24 kHz WAV bytes
    │ interrupt_controller.cancelled checked before each synthesis task
    ▼
audio_out_queue → audio_output_worker (Worker 5)
    │ sends WAV bytes as binary frames over /ws WebSocket
    │ manages state: thinking → speaking (on first chunk) → listening (after last chunk)
    │ on interrupt: drains queue, broadcasts {"type": "status", "state": "listening"}
    ▼
WSClient receives binary WAV frame
    │
    ▼
AudioPlayback.enqueue(wav_bytes)
    │ _play_wav_blocking: decodes WAV → float32 numpy array → sounddevice.play(blocking=True)
    ▼
[Speaker output]
```

**Streaming vs batch:**

- STT: **batch** — entire audio buffer sent as one API call after VAD silence
- LLM Call 1 (intent): **streaming** (token-by-token) but treated as batch (awaits complete JSON)
- LLM Call 2 (response): **true streaming** — tokens pushed to `token_queue` one at a time
- TTS: **sentence-by-sentence** — synthesis triggered at each sentence boundary; not word-by-word or full-response
- Audio output: **chunk-by-chunk** — each synthesised WAV sentence is sent independently

**Local vs external:**

- STT: **external** (Groq Whisper API)
- LLM: **external** (Groq LLaMA primary, Gemini fallback)
- TTS: **local** (Kokoro, CPU-only PyTorch, runs in-process)
- VAD: **local** (Silero VAD TorchScript model, in-process)
- Search: **external** (Tavily API, optional)

---

## Language handling

EN/JA switching operates at two levels:

**1. Detection — STT path (voice input):** Groq Whisper returns an ISO 639-1 `language` field in `verbose_json` format. `LanguageDetector.detect()` maps this to `"en"` or `"ja"` (defaults to `"en"` for anything else). The detected code is stored in `PipelineState.detected_language`.

**2. Detection — text input path (browser text box):** Text submitted via the `/ws/ui` `text_input` message is always tagged `language="en"` in the `TranscriptionResult`. Per-text language detection is not done at the STT stage for this path.

**3. TTS routing:** `TTSRouter.synthesize()` calls `_detect_language_from_text()`, which checks for CJK/Hiragana/Katakana Unicode ranges (U+3000–U+9FFF, U+3040–U+30FF, etc.). If any CJK characters are found, `KokoroJapaneseTTS` (`lang_code='j'`, voice `jf_alpha`) is used regardless of the `language` parameter passed. This means mixed-language responses automatically route Japanese sentences to the Japanese voice, even when STT reported "en". English uses `KokoroTTS` (`lang_code='a'`, voice `af_heart`).

**4. Prompt assembly:** `PromptAssembler._select_language_block()` picks `lang_ja.txt`, `lang_en.txt`, or `lang_unknown.txt` based on `IntentResult.language` (returned by the LLM classifier on Call 1, not the STT detector). If `deployment_config.tone_override == "formal"` and language is `"ja"`, the optional `lang_ja_formal.txt` is loaded (falls back to `lang_ja.txt` if absent).

**5. Out-of-scope and clarification responses:** Hardcoded templates in `Router.CLARIFICATION_TEMPLATES` and `deployment.yaml` `out_of_scope_response` provide responses in both languages, selected by `detected_language`.

---

## Search capability

**What is searched:** The open web, via the Tavily search API.

**Data source:** External — `TavilySearchClient` wraps the `tavily-python` SDK, which calls the Tavily REST API. No local index or vector store exists.

**How results are returned:** `TavilySearchClient.search()` returns a newline-joined string of `"title: content"` pairs from the Tavily response (up to however many results Tavily returns; no explicit limit is set client-side). This string is passed as `retrieved_context` in `RouteResult` and injected into the system prompt as a "Search Results" block by `PromptAssembler._get_route_context_block()`.

**RAG (environment route):** Phase 1 only — no embedding/vector retrieval. `Router._retrieve_environment_context()` reads files listed in `deployment.yaml`'s `environment_docs` array (plain text/markdown), concatenates them, and injects the raw text. Token-budget truncation is noted as a TODO and not yet implemented.

**When search is enabled:** Only when `TAVILY_API_KEY` is set **and** `web_search_enabled: true` in `deployment.yaml`. If either condition is false, `web_search` intent falls back to the `general` route.

---

## Exe packaging

**Tool:** PyInstaller 6.10.0 (confirmed in `builder.log`)

**Entry point:** `launcher.py` in the project root (source file deleted after build; referenced in `builder.log` at line `12037 DEBUG: script: ...launcher.py` and compiled as the main entry point)

**Output:** `dist/DemoVoiceAssistant/DemoVoiceAssistant.exe` — one-directory bundle (not onefile); the `dist/DemoVoiceAssistant/` folder contains the exe plus all collected DLLs and data files

**What is bundled:**

- Python 3.11 runtime (via `python311.dll`)
- All Python packages from the venv, including full Kokoro + PyTorch CPU-only
- `espeakng_loader` + espeak-ng-data (used by Kokoro/misaki for text normalisation)
- `pyopenjtalk` + HTS voice data (Japanese TTS phonetics)
- `unidic_lite` dictionary (Japanese morpheme analysis)
- `en_core_web_sm` spaCy model (English NLP)
- `_sounddevice_data/portaudio-binaries/libportaudio64bit.dll` (microphone/speaker I/O)
- `_soundfile_data/libsndfile_64bit.dll` (WAV encoding/decoding)
- `misaki` data files (grapheme-to-phoneme data for EN and JA)
- Custom runtime hooks: `hooks/rthook_paths.py`, `hooks/rthook_transformers.py`
- Application icon: `assets/icon.ico`
- Version info from `version_info.txt`

**What is NOT bundled (confirmed absent from builder.log):** The `ui/dist/` SPA files and `server/prompts/` text files are **not** visible in the builder log as explicitly collected data — this cannot be confirmed as bundled from the available evidence. The `models/silero_vad.jit` file also does not appear as an explicit datas entry in the log.

**Startup sequence:** `launcher.py` (source deleted) was the entry point; based on what the built-in start scripts do, it likely started uvicorn serving `server.main:app` and the `client.main` audio process, then opened the browser. The exact `launcher.py` logic cannot be confirmed — the source no longer exists in the workspace.

---

## Configuration surface

|Variable / Key|Source|Default|Purpose|
|---|---|---|---|
|`GROQ_API_KEY`|`.env`|**required**|Groq API authentication (STT + LLM)|
|`GEMINI_API_KEY`|`.env`|`""`|Gemini fallback LLM (optional)|
|`TAVILY_API_KEY`|`.env`|`""`|Web search (optional)|
|`GROQ_STT_MODEL`|`.env`|`whisper-large-v3-turbo`|Groq Whisper model|
|`GROQ_LLM_MODEL`|`.env`|`llama-3.3-70b-versatile`|Groq LLaMA model|
|`VAD_SILENCE_MS`|`.env`|`600`|Silence duration before speech_end event|
|`LOG_LEVEL`|`.env`|`INFO`|Python logging level|
|`SERVER_HOST`|`.env`|`0.0.0.0`|uvicorn bind address|
|`SERVER_PORT`|`.env`|`8000`|HTTP + `/ws/ui` port|
|`WS_PORT`|`.env`|`8000`|AudioClient `/ws` port (same server)|
|`VITE_AUTO_CLOSE_MIC`|`.env` (Vite)|`true`|Auto-close mic after VAD silence|
|`deployment_id`|`config/deployment.yaml`|`"default"`|Identifier for this deployment|
|`deployment_type`|yaml|`"desktop"`|`reception` / `enterprise` / `desktop` — sets role description in prompt|
|`location_name`|yaml|`"Robo Assistant"`|Injected into system prompt|
|`language_primary`|yaml|`"en"`|Primary language (used by assembler)|
|`language_secondary`|yaml|`"ja"`|Secondary language|
|`tone_override`|yaml|`null`|`"formal"` selects `lang_ja_formal.txt`|
|`environment_docs`|yaml|`[]`|List of local files for RAG retrieval|
|`session_memory_turns`|yaml|`6`|Conversation history depth|
|`web_search_enabled`|yaml|`true` (in file)|Enable Tavily web search route|
|`out_of_scope_response`|yaml|EN/JA strings|Direct OOS response by language|
|Silence threshold|hardcoded|32ms frames|Frame size in `audio_capture.py`|
|TTS voice (EN)|hardcoded|`af_heart`|Kokoro English voice|
|TTS voice (JA)|hardcoded|`jf_alpha`|Kokoro Japanese voice|
|TTS sample rate|hardcoded|24 kHz|Kokoro output sample rate|
|Mic sample rate|hardcoded|16 kHz|AudioCapture input sample rate|
|LLM Call 1 params|hardcoded|max_tokens=150, temp=0.0|Intent classification|
|LLM Call 2 params|hardcoded|max_tokens=200, temp=0.65|Response generation|
|History token cap|hardcoded|600 (word-count)|PromptAssembler trim threshold|
|Comma flush threshold|hardcoded|60 chars|TTSRouter soft boundary|
|Hard flush threshold|hardcoded|120 chars|TTSRouter safety net|
|Barge-in cooldown|hardcoded|0.5 s|VAD echo-protection window|
|Barge-in debounce|hardcoded|1.0 s|VAD repeat-fire protection|

---

## Dependency inventory

### Python dependencies

|Package|Pinned version|Purpose|
|---|---|---|
|fastapi|0.115.5|HTTP + WebSocket server framework|
|uvicorn[standard]|0.32.1|ASGI server|
|websockets|13.1|WebSocket client (used in `client/ws_client.py`)|
|python-dotenv|1.0.1|`.env` file loading|
|pydantic|2.10.3|Data validation (FastAPI dependency)|
|httpx|0.28.1|Async HTTP client (used by groq SDK)|
|groq|0.13.0|Groq STT (Whisper) + LLM (LLaMA) API client|
|google-generativeai|0.8.3|Gemini LLM fallback API client|
|kokoro|0.9.4|Local TTS synthesis engine (EN + JA)|
|misaki[en,ja]|0.9.4|G2P (grapheme-to-phoneme) for Kokoro|
|torch|2.5.1+cpu|PyTorch (Silero VAD inference + Kokoro)|
|torchaudio|2.5.1+cpu|Torch audio utilities|
|soundfile|0.12.1|WAV encode/decode (libsndfile wrapper)|
|sounddevice|0.5.1|Microphone capture + speaker playback (PortAudio)|
|numpy|1.26.4|Audio array manipulation|
|tavily-python|0.5.0|Tavily web search API client|
|pytest|8.3.4|Test runner|
|pytest-asyncio|0.24.0|Async test support|
|hypothesis|≥6.152.7 (dev)|Property-based testing|
|PyInstaller|6.10.0|Windows exe packaging (in venv, not in requirements.txt)|
|pyyaml|(transitive)|YAML parsing for `deployment.yaml`|

### JavaScript / TypeScript dependencies

|Package|Version|Purpose|
|---|---|---|
|react|^18.3.1|UI framework|
|react-dom|^18.3.1|DOM renderer|
|vite|^5.4.11|Build tool + dev server|
|@vitejs/plugin-react|^4.3.3|Vite React plugin (Babel fast-refresh)|
|typescript|^5.6.3|Type checking|
|tailwindcss|^3.4.15|Utility-first CSS|
|postcss|^8.4.49|CSS processing|
|autoprefixer|^10.4.20|CSS vendor prefixes|
|vitest|^4.1.6|Test runner|
|@vitest/ui|^4.1.6|Vitest browser UI|
|@testing-library/react|^16.3.2|React component testing|
|@testing-library/jest-dom|^6.9.1|DOM matchers|
|@testing-library/user-event|^14.6.1|User interaction simulation|
|fast-check|^4.8.0|Property-based testing|
|jsdom|^29.1.1|DOM simulation for Vitest|
|@types/react|^18.3.12|TypeScript types|
|@types/react-dom|^18.3.1|TypeScript types|

---

## Modularisation assessment

The pipeline is organised into well-separated classes with clean dependency injection, but everything runs in a single uvicorn process. Here is an assessment of what could be extracted as standalone HTTP endpoints with minimal vs significant work:

**Could be extracted with minimal refactoring (thin HTTP wrapper only):**

- **STT endpoint** — `GroqSTTBackend.transcribe(pcm16_bytes)` is already a pure async function taking bytes and returning a dataclass. A `POST /api/stt` endpoint accepting a WAV/PCM file upload and returning `{"text": "...", "language": "..."}` would require ~20 lines of new FastAPI code and zero changes to the backend class.
    
- **TTS endpoint** — `TTSRouter.synthesize(text, language)` is similarly pure. A `POST /api/tts` accepting `{"text": "...", "language": "en|ja"}` and returning `audio/wav` bytes would be equally trivial. Sentence-boundary accumulation (`TTSRouter.accumulate`) is pipeline-state-specific and would stay in the pipeline, but single-shot synthesis is already separated.
    
- **Language detection endpoint** — `LanguageDetector.detect()` is stateless. A `POST /api/detect-language` would be a one-liner.
    
- **Search endpoint** — `TavilySearchClient.search(query)` has no pipeline dependencies. A `POST /api/search` would be straightforward.
    

**Would require moderate refactoring:**

- **Intent classification endpoint** — `IntentClassifier.classify(transcript)` is nearly pure but depends on `LLMChain` (which holds the Groq client). Extracting it requires either injecting the chain via FastAPI dependency or initialising a separate instance. The main complication is that Call 1 and Call 2 share the same `LLMChain` — they would need to be separated into distinct endpoint-level resources.
    
- **Full LLM response endpoint** — The two-call pipeline (classify → route → assemble → stream) is orchestrated in `_llm_worker`, which is tightly coupled to `asyncio.Queue`, `InterruptController`, and `PipelineState`. Extracting it as a streaming `POST /api/chat` (returning `text/event-stream`) would require pulling the per-turn logic out of `_llm_worker` into a standalone function — approximately 80–100 lines of refactoring.
    

**Would require significant refactoring:**

- **Full voice pipeline as a REST service** — The 5-worker pipeline is fundamentally stateful (queues, interrupt controller, session history) and designed around a long-lived WebSocket connection. Converting it to request/response HTTP semantics would require a new session management layer (session tokens, server-side state store) and is a deeper architectural change.
    
- **Streaming audio pipeline** — The audio binary protocol (raw PCM16 in, WAV chunks out, with mid-stream interrupt signalling) has no natural HTTP equivalent; it requires a persistent binary channel. The WebSocket design is appropriate and would need to be retained or replaced with HTTP/2 Server Push, which has worse tooling support.
    

**What would need to change for any modularisation:** CORS middleware would need to be added (currently absent). The `app.state` singleton pattern would need to be replaced by FastAPI dependency injection (`Depends`) so components can be instantiated and shared cleanly across endpoints.