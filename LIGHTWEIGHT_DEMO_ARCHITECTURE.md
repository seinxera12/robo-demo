# Lightweight Demo Architecture Blueprint
## Voice Kiosk Chatbot — Demo Edition

**Document version:** 1.0  
**Based on audit:** Voice Kiosk Chatbot Technical Audit v1.0 (May 10, 2026)  
**Purpose:** Master blueprint for building a fast, portable, demo-reliable voice assistant  

---

## Table of Contents

1. [Minimal Feature Set Definition](#1-minimal-feature-set-definition)
2. [New Lightweight Architecture](#2-new-lightweight-architecture)
3. [Technology Replacement Plan](#3-technology-replacement-plan)
4. [Dependency Minimization Strategy](#4-dependency-minimization-strategy)
5. [Packaging & Deployment Strategy](#5-packaging--deployment-strategy)
6. [Estimated Resource Reduction](#6-estimated-resource-reduction)
7. [Demo UX Design Priorities](#7-demo-ux-design-priorities)
8. [Failure Isolation Strategy](#8-failure-isolation-strategy)
9. [Final Recommended Stack](#9-final-recommended-stack)
10. [Final Deliverable](#10-final-deliverable)

---

## 1. Minimal Feature Set Definition

### 1.1 Feature Triage Table

| Feature | Decision | Reason |
|---------|----------|--------|
| **Real-time voice input (mic)** | ✅ KEEP | Core demo wow-factor. Cannot simulate. |
| **Voice Activity Detection (Silero VAD)** | ✅ KEEP | Already tiny (2 MB). Removes need for push-to-talk. Seamless UX. |
| **Speech-to-Text** | ✅ KEEP via API | Required. Replace local Whisper with Groq Whisper API. |
| **LLM inference** | ✅ KEEP via API | Required. Replace Ollama with Groq LLaMA or Gemini Flash. |
| **Streaming LLM tokens** | ✅ KEEP | Streaming is what makes the demo feel alive. Critical UX. |
| **Sentence-boundary TTS streaming** | ✅ KEEP | Low perceived latency. Retain this architecture pattern. |
| **Text-to-Speech** | ✅ KEEP — local Kokoro | Kokoro-82M is already lightweight (330 MB, CPU-only). Sounds excellent. Avoids another API key and round-trip. |
| **Barge-in / interrupt** | ✅ KEEP | High demo value. Feels like real conversation. Easy to implement. |
| **Conversational memory (10-turn)** | ✅ KEEP | Required for coherent multi-turn demos. Trivial to implement in-process. |
| **Status display (listening/thinking/speaking)** | ✅ KEEP | Communicates pipeline state. Reduces confusion during demos. |
| **WebSocket transport** | ✅ KEEP | Required for real-time audio streaming between processes. |
| **Text input fallback** | ✅ KEEP | Allows demos without a microphone or when audio fails. |
| **Web search (Tavily)** | ✅ KEEP as OPTIONAL | Easy to enable with one API key. Impressive in demos ("what's the weather today?"). Off by default. |
| **Language detection (EN/JA)** | ✅ KEEP — simplified | Use Whisper's detected language from the API response. One line of code. |
| **Modern web UI** | ✅ NEW — replace PyQt6 | Browser-based UI eliminates Qt6 install hell. Works on any OS. Far easier to style. |
| **RAG / ChromaDB / Building KB** | ❌ REMOVE | Requires multilingual-e5-large (560 MB), ChromaDB, ingestion pipeline. Not meaningful in a generic demo. |
| **Intent classifier (embedding-based)** | ❌ REMOVE | Tied to RAG. Without a KB, "BUILDING" intent is meaningless. Keyword classifier only if web search is on. |
| **KokoClone / voice cloning** | ❌ REMOVE | Python 3.12 split venv, Kanade VC model (1–2 GB), C++ build deps. Breaks too often. |
| **vLLM backend** | ❌ REMOVE | Commented out in original. Requires Docker + CUDA. Not needed. |
| **Ollama backend** | ❌ REMOVE | 4.7 GB model download, Docker dependency, GPU needed. Replaced by Groq API. |
| **SearXNG (self-hosted)** | ❌ REMOVE | Docker dependency. Replaced by Tavily API if search is needed. |
| **CosyVoice2 / VOICEVOX** | ❌ REMOVE | Already inactive in original. Pure dead weight. |
| **ChromaDB** | ❌ REMOVE | No RAG = no vector store needed. |
| **deepspeed / tensorrt** | ❌ REMOVE | Not used in main pipeline. Only pulled in as indirect deps. Major install pain. |
| **PyQt6 UI** | ❌ REMOVE | Replaced by browser-based React UI. |
| **Opus encoder** | ❌ REMOVE | Present but unused in original. |
| **LLM tool definitions** | ❌ REMOVE | Defined but not actively used in original. |
| **vLLM/Grok/Ollama fallback chain** | 🔄 SIMPLIFY | Replace with: Groq primary → Gemini Flash fallback. Two-step chain, both API-based. |
| **Multi-language TTS routing** | 🔄 SIMPLIFY | Keep EN and JA Kokoro pipelines. Remove KokoClone entirely. TTSRouter stays, simplified. |
| **Building KB document store** | 🟡 OPTIONAL | Could be re-added as a simple JSON/markdown file lookup for domain-specific demos. No embeddings needed — keyword match is enough for a demo. |
| **Audio visualization** | 🟡 OPTIONAL | Nice-to-have in UI. Waveform/pulse animation during listening/speaking states. |

### 1.2 What Changes and Why — Summary

The three biggest cuts are:
1. **Remove all local heavy models** (Whisper, Ollama/Qwen, multilingual-e5, KokoClone). Combined they require ~7–9 GB VRAM, 2–5 hours of setup, and are the root of every machine-specific failure. Replaced by 2–3 API keys.
2. **Remove all Docker services** (Ollama, SearXNG, VOICEVOX, CosyVoice). Docker adds setup friction on client demo machines (NVIDIA Container Toolkit, WSL2 configuration). Everything becomes native Python.
3. **Replace PyQt6 with a browser UI**. Qt6 has platform-specific font/display/audio issues (especially WSL2/Windows). A browser UI is universal, easier to style, and instantly impressive.

The one significant local model **kept** is Kokoro-82M TTS — because it's already small (330 MB), runs on CPU, sounds good, and avoids introducing TTS API latency into the demo's most perceptible quality dimension (voice naturalness).

---

## 2. New Lightweight Architecture

### 2.1 System Overview

The demo version collapses to **two processes** (same as original) with a dramatically simplified dependency graph. The server is a single Python process with no external services.

```
┌──────────────────────────────────────────────────────────────────┐
│  CLIENT PROCESS  (any OS, Python 3.11, CPU only)                 │
│                                                                  │
│  Microphone ──► sounddevice ──► SileroVAD ──► WebSocket ──►      │
│                                                                  │
│  ◄── WebSocket ──► AudioPlayback ──► Speaker                      │
│                                                                  │
│  Browser UI (localhost:3000) — React/Vite, served by FastAPI     │
│    Displays: transcript, LLM stream, status indicator            │
│    Controls: push-to-talk fallback, text input, interrupt        │
└──────────────────────────────────────────────────────────────────┘
                        │  ws://localhost:8765/ws
                        │  Binary PCM16 (up) / Binary WAV 24kHz (down)
                        │  JSON control messages (both directions)
┌──────────────────────────────────────────────────────────────────┐
│  SERVER PROCESS  (Python 3.11, CPU only, no Docker)              │
│                                                                  │
│  FastAPI (8765 WS + 8000 HTTP)                                   │
│                                                                  │
│  WebSocket ──► audio_input_worker                                │
│                    │                                             │
│                    ▼                                             │
│              Groq STT API ──► transcript                         │
│                    │                                             │
│                    ▼                                             │
│              llm_worker                                          │
│                    │                                             │
│         ┌──────────┴──────────┐                                  │
│         │  (optional)         │                                  │
│     Tavily Search API    keyword check                           │
│         │                     │                                  │
│         └──────────┬──────────┘                                  │
│                    ▼                                             │
│              LLMChain                                            │
│           (Groq primary → Gemini Flash fallback)                 │
│                    │  streaming tokens                           │
│                    ▼                                             │
│              tts_worker                                          │
│           sentence boundary accumulation                         │
│                    │                                             │
│              KokoroTTS (local, CPU, 330 MB)                      │
│           EN: KPipeline('a') / JA: KPipeline('j')               │
│                    │  WAV chunks                                 │
│                    ▼                                             │
│         audio_output_worker ──► WebSocket ──► Client             │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Service Count Comparison

```
ORIGINAL SYSTEM                    DEMO SYSTEM
──────────────────                 ──────────────
voice-server (Python)              voice-server (Python)    ← kept
Ollama (Docker)                    [removed]
vLLM (Docker, disabled)            [removed]
SearXNG (Docker)                   [removed]
KokoClone service (Python 3.12)    [removed]
CosyVoice2 (Docker, inactive)      [removed]
VOICEVOX (Docker, inactive)        [removed]
client (Python, PyQt6)             client (Python, headless audio only)
                                   browser UI (static files served by FastAPI)

Original: 7 processes (3 active Docker + 2 Python + 2 inactive Docker)
Demo:     2 processes (1 Python server + 1 browser tab)
```

### 2.3 Streaming Pipeline Flow

```
[VOICE INPUT]
User speaks
    │
    ▼ sounddevice InputStream @ 16kHz PCM16, 32ms frames
SileroVAD (CPU, 2 MB torch model)
    │  speech_end detected after 700ms silence
    ▼ accumulated PCM16 buffer
WebSocket.send_bytes(pcm16_audio)
    │
    ├─────────────────── NETWORK (LAN or loopback) ───────────────────┤
    │
    ▼ [SERVER] audio_input_worker
Groq STT API  (HTTPS, ~200–400ms round-trip)
    │  whisper-large-v3-turbo model
    │  returns: {text, language, duration}
    ▼ transcript + detected_language → transcript queue

[LLM INFERENCE]
    ▼ [SERVER] llm_worker
keyword_intent_check()
    │  "search for / what is / latest" → SEARCH intent (if Tavily enabled)
    │  else → GENERAL
    ├── SEARCH: Tavily API call → format_context() → prepend to messages
    └── GENERAL: empty context
    │
build_messages(history, context, transcript)
    │  10-turn sliding window, ~3000 token budget
    ▼
Groq API (streaming, llama-3.3-70b-versatile or llama-3.1-8b-instant)
    │  Server-Sent Events stream
    │  each token → token queue + ws JSON {"type":"llm_text_chunk","text":"..."}
    ▼ token stream

[TTS SYNTHESIS]
    ▼ [SERVER] tts_worker
accumulate tokens until sentence boundary (.?!。？！)
    │  typically 3–15 tokens per sentence
    ▼ sentence string
language_router(detected_lang)
    ├── EN → KokoroTTS: KPipeline(lang_code='a') → 24kHz WAV
    └── JA → KokoroJapaneseTTS: KPipeline(lang_code='j') → 24kHz WAV
    │  CPU inference, ~150–400ms per sentence
    ▼ WAV bytes → audio_output queue

[AUDIO OUTPUT]
    ▼ [SERVER] audio_output_worker
WebSocket.send_bytes(wav_chunk)
    │
    ├─────────────────── NETWORK ─────────────────────────────────────┤
    │
    ▼ [CLIENT] AudioPlayback
wave.open(io.BytesIO(wav_bytes)) → PCM16 numpy array
sounddevice OutputStream @ 24kHz → Speaker
```

### 2.4 Request/Response Flow (Single Turn)

```
T=0ms      User stops speaking → VAD fires speech_end
T=0ms      Client sends PCM16 bytes over WebSocket
T=200ms    Groq STT API responds (network + model)
T=210ms    llm_worker: intent check (synchronous, <1ms)
T=220ms    Groq LLM API: first token arrives (streaming)
T=250ms    tts_worker: first sentence accumulated
T=400ms    Kokoro synthesizes first sentence WAV
T=450ms    Client receives first WAV chunk → playback begins

★ Time-to-First-Audio (TTFA): ~400–600ms
  (vs original ~1.5–4s, limited by GPU contention and local Whisper)
```

### 2.5 State Machine (unchanged from original, simplified workers)

```
listening ──► [speech_end] ──► thinking ──► [first_audio] ──► speaking
    ▲                                                              │
    └────────────────── [interrupt | playback_done] ──────────────┘
```

---

## 3. Technology Replacement Plan

### 3.1 Component Replacement Table

| Original Component | Lightweight Replacement | Reason |
|---|---|---|
| **Whisper Large V3 (local, GPU)** | **Groq Whisper API** (`whisper-large-v3-turbo`) | Eliminates faster-whisper, ctranslate2, CUDA 12.1 dependency. Free tier: 14,400 req/day. Latency ~200–400ms comparable to GPU. |
| **Ollama + Qwen2.5-7B (Docker, GPU)** | **Groq LLaMA 3.3-70b-versatile** (primary) | Eliminates Docker, 4.7 GB model download, 5+ GB VRAM. Groq free tier generous. 70B model quality far exceeds local 7B. TTFT ~100–200ms. |
| **Grok API (cloud fallback)** | **Gemini 2.0 Flash** (fallback) | Different provider = true fallback. Free tier. Multimodal option for future. |
| **vLLM (Docker, disabled)** | Removed entirely | Was already commented out. No value to keep. |
| **LLMFallbackChain (3-tier)** | **2-tier API chain** (Groq → Gemini) | Simpler. Both APIs. No local backends. |
| **multilingual-e5-large (560 MB, CPU)** | Removed entirely | Only needed for RAG + embedding-based intent. Neither is in demo. |
| **IntentClassifier (embedding)** | **Keyword regex** (10 lines) | Sufficient for demo. No model. No latency. |
| **BuildingKB / ChromaDB / RAG** | Removed entirely | Domain-specific to kiosk deployment. Not relevant for a generic demo. Can be re-added as JSON lookup if needed. |
| **SearXNG (self-hosted Docker)** | **Tavily API** (optional) | Single pip package (`tavily-python`). No Docker. No self-hosting. Free tier: 1,000 searches/month. |
| **KokoroTTS (local, CPU)** | **KokoroTTS (local, CPU)** — unchanged | Already optimal. 330 MB. CPU-only. Good voice quality. pip installable. Keep as-is. |
| **KokoCloneTTS + Kanade VC** | Removed entirely | Python 3.12 venv split, C++ build, 1–2 GB model. Voice cloning not needed for demo. |
| **KokoroJapaneseTTS (local, CPU)** | **KokoroJapaneseTTS** — unchanged | Same model as EN, different lang_code. Already minimal. |
| **TTSRouter** | **Simplified TTSRouter** | Remove KokoClone HTTP path. EN/JA Kokoro routing only. ~30 lines. |
| **PyQt6 UI (fullscreen kiosk)** | **React + Vite browser UI** | No Qt6 install. Works on any OS. Easier to make look impressive. Served directly from FastAPI as static files. |
| **sounddevice (client audio)** | **sounddevice** — unchanged | Best Python audio lib for low-level PCM. Cross-platform. Keep as-is. |
| **SileroVAD (client, CPU)** | **SileroVAD** — unchanged | Already 2 MB, CPU-only. Perfect. |
| **FastAPI + uvicorn** | **FastAPI + uvicorn** — unchanged | No reason to change. |
| **asyncio pipeline (5 workers)** | **asyncio pipeline (5 workers)** — unchanged | Core architecture. Keep the queue design. |
| **WebSocket protocol** | **WebSocket protocol** — unchanged | Same binary + JSON message format. |
| **Docker Compose** | **Removed** | No services left to containerize. Everything runs natively. |
| **Two Python venvs** | **One Python venv** | KokoClone removal eliminates the 3.12 split. Single 3.11 venv. |
| **deepspeed, tensorrt, spacy** | Removed entirely | Not used in main pipeline. Install pain with zero benefit. |
| **CUDA / cuDNN / NCCL** | Removed entirely (for demo) | Kokoro runs on CPU. STT/LLM moved to APIs. Zero GPU requirements. |

### 3.2 Quality, Cost, and Latency Tradeoffs

| Dimension | Original | Demo | Notes |
|---|---|---|---|
| **STT accuracy** | Excellent (Whisper Large V3) | Excellent (Whisper Large V3 Turbo via Groq) | Same model family. Minimal quality difference. |
| **LLM quality** | Good (Qwen2.5-7B Q4) | Better (LLaMA 3.3-70B via Groq) | Demo version actually has BETTER LLM quality. 70B > 7B. |
| **TTS voice quality** | Good (Kokoro-82M) | Good (Kokoro-82M) | Identical — same model. |
| **Voice cloning (JA)** | Yes (Kanade VC) | No — standard Kokoro JA | Demo loses custom voice. Acceptable trade. |
| **RAG context** | Building KB context | None (general knowledge) | Demo loses domain answers. Acceptable for generic demo. |
| **Web search** | Yes (SearXNG, self-hosted) | Optional (Tavily API) | Quality equivalent. Setup massively simpler. |
| **Offline capability** | Full (except Grok fallback) | None (all API-dependent) | Intentional sacrifice for demo simplicity. |
| **TTFA latency** | 1.5–4s (typical) | 0.4–0.8s (estimated) | Demo is likely **faster** due to Groq's speed vs local GPU contention. |
| **API cost (Groq)** | $0 | ~$0 on free tier for demos | 14,400 STT req/day; LLM free tier generous. |
| **API cost (Tavily)** | $0 | ~$0 on free tier | 1,000 searches/month free. |

---

## 4. Dependency Minimization Strategy

### 4.1 Python Version and Environment

- **Python version: 3.11.x** (pinned)
  - Same as original server venv. Well-tested with all remaining deps.
  - 3.12 was only needed for KokoClone (now removed).
  - 3.13 is too new for some transitive deps.
  - Use `python3.11` specifically. Install via `pyenv` or system package manager.

- **Package manager: `uv`**
  - Replaces pip for speed. `uv pip install` is 10–100× faster than pip.
  - Deterministic lockfile (`uv.lock`).
  - Single binary, no system-level side effects.
  - Cross-platform (Linux, macOS, Windows).
  - Especially valuable for client demo machines where you need fast, reliable installs.

- **Virtual environment**: single `venv/` at project root, managed by uv.

### 4.2 Docker vs Native

**Decision: No Docker for the demo.**

Docker is eliminated entirely because:
- No remaining services require containers (Ollama, SearXNG, VOICEVOX, CosyVoice all removed)
- Docker on Windows requires WSL2 configuration, NVIDIA Container Toolkit, and is a significant demo setup burden
- The one remaining local model (Kokoro) is pip-installable
- Native Python is simpler to debug during demos

### 4.3 GPU Requirement

**Decision: Zero GPU requirement.**

- Kokoro TTS: runs on CPU (original already set `KOKORO_DEVICE=cpu`)
- STT: moved to Groq API
- LLM: moved to Groq API
- VAD: CPU only (always was)
- No CUDA, no cuDNN, no NCCL, no torch CUDA build

The demo runs identically on a MacBook, a Windows laptop, or a Linux desktop.

### 4.4 Minimal Dependency Set

```
# requirements.txt (demo edition)
# ~500 MB total install (vs 8–12 GB original)

# Server
fastapi>=0.115.0,<1.0.0
uvicorn[standard]>=0.30.0,<1.0.0
websockets>=13.0,<16.0
python-dotenv>=1.0.0,<2.0.0
pydantic>=2.0.0,<3.0.0
httpx>=0.27.0,<1.0.0

# STT + LLM APIs
groq>=0.12.0,<1.0.0           # Groq SDK: Whisper STT + LLaMA LLM
google-generativeai>=0.8.0    # Gemini fallback

# TTS (local)
kokoro>=0.9.4,<1.0.0          # Kokoro-82M TTS engine
torch>=2.1.0,<3.0.0           # CPU-only torch (no +cu121 variant!)
torchaudio>=2.1.0,<3.0.0      # Audio processing
soundfile>=0.12.0              # WAV I/O for Kokoro output
misaki>=0.9.4,<1.0.0          # G2P phonemiser for Kokoro

# Client audio
sounddevice>=0.4.6,<1.0.0     # Microphone + speaker I/O
silero-vad>=5.0,<6.0          # OR: load via torch.hub (2 MB, auto-download)

# Optional search
tavily-python>=0.3.0,<1.0.0   # Optional: web search

# Japanese TTS support (optional but small)
pyopenjtalk>=0.3.0             # Only needed for JA phonemisation
                               # Requires CMake + C++ — make optional
```

### 4.5 Pinning Strategy

| Dependency | Pinning | Reason |
|---|---|---|
| `fastapi` | `>=0.115,<1.0` | Flexible. API is stable. |
| `torch` | `>=2.1.0,<3.0` | CPU build. Flexible within major version. |
| `kokoro` | `>=0.9.4,<1.0` | Pin minor. Breaking changes possible. |
| `groq` | `>=0.12.0,<1.0` | SDK evolves. Minor pin. |
| `websockets` | `>=13.0,<16.0` | Protocol-critical. Bound range. |
| `sounddevice` | `>=0.4.6,<1.0` | Audio I/O. Known good version. |
| All others | Flexible (minor range) | Low risk of breaking change. |

**Use `uv lock` to generate a deterministic lockfile. Commit it. This is the single biggest reliability improvement for demos — reproducible installs every time.**

### 4.6 System-Level Dependencies

| System Dep | Original | Demo | Notes |
|---|---|---|---|
| CUDA toolkit | Required | Not required | Eliminated entirely |
| cuDNN | Required | Not required | Eliminated |
| NCCL | Required | Not required | Eliminated |
| NVIDIA drivers | Required | Not required | Eliminated |
| Docker + NVIDIA Container Toolkit | Required | Not required | Eliminated |
| `build-essential` + `cmake` | Required (deepspeed, pyopenjtalk) | Optional (pyopenjtalk only) | Only if JA TTS needed |
| `fonts-noto-cjk` | Required (PyQt6 JA rendering) | Not required | Browser handles fonts |
| Python 3.11 | Required | Required | Same |
| `portaudio19-dev` (Linux) | Required | Required | sounddevice dependency |

On macOS: `brew install portaudio` only.  
On Windows: no system deps needed (sounddevice ships PortAudio binaries).

---

## 5. Packaging & Deployment Strategy

### 5.1 Options Analysis

| Approach | Setup Time | Portability | Demo Reliability | Complexity |
|---|---|---|---|---|
| **Docker Compose** | Medium (10–20 min) | High | Medium (Docker required) | Medium |
| **Electron app** | Long to build | High | High | Very High |
| **Portable installer (.exe/.sh)** | Fast (5 min) | High | High | High to build |
| **Single-script launcher** | Fast (5–10 min) | Medium | High | Low |
| **Cloud-hosted backend + local frontend** | Instant | Very High | Medium (network dep) | Medium |
| **Standalone backend + browser frontend** ★ | Fast (5–10 min) | Very High | High | Low |

### 5.2 Recommended Approach: Single-Script Setup + Browser UI

**Primary: Native Python backend + browser-based frontend, launched by a single shell script.**

```
demo-voice-assistant/
├── setup.sh          ← One-time setup: creates venv, installs deps, builds UI
├── start.sh          ← Demo start: launches server + opens browser
├── start.bat         ← Windows equivalent
├── .env.example      ← Template with API key placeholders
├── requirements.txt  ← Pinned Python deps
├── uv.lock           ← Deterministic lockfile
├── server/           ← FastAPI backend (Python)
│   ├── main.py
│   ├── pipeline.py
│   ├── config.py
│   ├── stt/
│   ├── llm/
│   ├── tts/
│   └── search/
├── client/           ← Audio client (Python, headless)
│   ├── main.py
│   ├── audio_capture.py
│   ├── vad.py
│   ├── ws_client.py
│   └── audio_playback.py
└── ui/               ← React/Vite frontend (pre-built, static files)
    ├── dist/         ← Pre-built. Committed to repo. No Node.js needed to run.
    └── src/          ← Source (for development only)
```

**Setup sequence (one-time, ~5 minutes):**
```bash
git clone <repo>
cd demo-voice-assistant
cp .env.example .env
# Edit .env: add GROQ_API_KEY (and optionally TAVILY_API_KEY)
./setup.sh      # Creates venv, uv pip install, downloads Kokoro model
```

**Demo launch (30 seconds):**
```bash
./start.sh
# Opens browser automatically at http://localhost:8000
# Server starts, client audio starts
# Ready to demo
```

### 5.3 Why Not Docker for the Demo?

Docker is the right choice for production deployments. It is the wrong choice for demos because:
- Requires Docker Desktop installed on the demo machine
- Requires NVIDIA Container Toolkit for GPU (not needed but adds confusion)
- Container audio passthrough is non-trivial on all 3 OS platforms
- Cold start time (~30s) adds to demo setup
- Any Docker issue during a live demo is very hard to debug quickly

The native Python approach installs once, runs anywhere Python 3.11 runs, and is trivially debuggable.

### 5.4 Pre-built UI

The React UI is **pre-built and committed as static files** in `ui/dist/`. This means:
- Demo machines do not need Node.js installed
- FastAPI serves the static files directly: `app.mount("/", StaticFiles(directory="ui/dist"))`
- Zero build step at demo time

---

## 6. Estimated Resource Reduction

| Category | Original System | Demo Version | Reduction |
|---|---|---|---|
| **Disk Usage (Python envs)** | ~12–18 GB (venv + KokoClone venv) | ~1–1.5 GB (single venv, CPU torch) | **~90% reduction** |
| **Disk Usage (models)** | ~8–10 GB (Whisper + e5-large + Ollama + Kokoro + Kanade) | ~330 MB (Kokoro only) | **~97% reduction** |
| **Disk Usage (Docker images)** | ~5–8 GB (Ollama + SearXNG + VOICEVOX + CosyVoice) | 0 GB | **100% reduction** |
| **Total Disk** | ~25–35 GB | ~2–2.5 GB | **~93% reduction** |
| **RAM Usage (server, idle)** | ~3–4 GB (Whisper + e5-large loaded) | ~400–600 MB (Kokoro lazy-loaded) | **~85% reduction** |
| **RAM Usage (server, active)** | ~4–6 GB | ~700 MB–1 GB | **~83% reduction** |
| **VRAM Usage** | ~7–9 GB (Whisper + Ollama, causes OOM) | 0 GB | **100% reduction** |
| **Setup Time (first time)** | 2–5 hours (realistically 1–2 days with debugging) | 5–10 minutes | **~97% reduction** |
| **Services Running** | 7 (3 Docker + 2 Python + 2 inactive Docker) | 2 (1 Python server + browser) | **~71% reduction** |
| **GPU Dependency** | Hard requirement (RTX 4050, 6 GB VRAM minimum) | None | **100% eliminated** |
| **Python Environments** | 2 (Python 3.11 + Python 3.12) | 1 (Python 3.11) | **50% reduction** |
| **Docker Containers** | 3 active (Ollama, SearXNG, VOICEVOX/CosyVoice) | 0 | **100% reduction** |
| **API Keys Required** | 0 (optional Grok key) | 1–2 (GROQ required, TAVILY optional) | Slight increase, but each is 1-minute signup |
| **Time-to-First-Audio** | 1.5–4s (typical) | ~0.4–0.8s (estimated) | **~75% faster** |

---

## 7. Demo UX Design Priorities

### 7.1 Interactions That Create the Strongest Demo Impression

**Tier 1 — Must be flawless:**
1. **First voice exchange**: The moment the user speaks and hears a response. This sets the entire tone. TTFA under 600ms feels like magic.
2. **Streaming text visible during thinking**: Watching tokens appear in real-time while the voice hasn't started yet signals intelligence and responsiveness. Never skip this.
3. **Voice quality**: Kokoro-82M at 24kHz sounds notably better than most demos. Lean into this.

**Tier 2 — High value:**
4. **Barge-in interrupt**: Saying something while the AI is speaking and having it stop immediately. Feels remarkably human. Most demos don't have this.
5. **Status indicator transitions**: `● LISTENING → ◉ THINKING → ▶ SPEAKING` with subtle animation. Communicates pipeline state. Reduces awkward silence anxiety.
6. **Multi-turn memory**: Ask a follow-up that only makes sense given the prior exchange. Demos coherent understanding.

**Tier 3 — Nice to have:**
7. **Web search answer**: "What's today's news about AI?" — live data impresses.
8. **Language switching**: Respond in Japanese if spoken to in Japanese.

### 7.2 Features That Are Unnecessary for Demos

- Voice cloning (too subtle; most people don't notice)
- Building KB / domain RAG (only relevant to the specific kiosk deployment)
- Custom system prompts (use a good default; don't expose config in the demo)
- Multi-kiosk support / kiosk IDs
- Transcription confidence scores
- PyQt6 fullscreen kiosk mode (browser is better for a demo setting)

### 7.3 Latency Optimizations That Matter Most

1. **Groq API cold-start**: The first API call in a session may have a ~100ms extra overhead. Solve with a silent "warm-up" POST at server startup.
2. **Kokoro first-load**: The model lazy-loads on first synthesis call (~2–5s). Pre-warm by synthesizing a silent phrase during the `lifespan` startup event. This prevents the first demo response from feeling slow.
3. **VAD silence threshold**: The original uses 800ms. Reduce to 600–700ms for demo. Feels snappier without cutting speech too aggressively.
4. **WebSocket connection**: Establish the WebSocket immediately on page load, before the user speaks. Do not connect on first speech.

### 7.4 UI Elements That Matter Most

```
┌─────────────────────────────────────────────────────┐
│  ● Voice Assistant                         [Settings]│
├─────────────────────────────────────────────────────┤
│                                                      │
│  [TRANSCRIPT DISPLAY]                                │
│  User: What can you tell me about quantum computing? │
│                                                      │
│  [LLM STREAMING DISPLAY]                             │
│  Assistant: Quantum computing uses the principles of │
│  quantum mechanics to process information in ways... │
│                                                      │
│  [AUDIO WAVEFORM / PULSE ANIMATION]                  │
│  ▁▂▄▆▄▂▁ (animates during listening/speaking)        │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  ◉ LISTENING  ·  ◌ THINKING  ·  ▷ SPEAKING  │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  [______ Type a message instead ______] [Send]       │
└─────────────────────────────────────────────────────┘
```

Critical UI decisions:
- **Status indicator** should be always visible and change color (green = listening, amber = thinking, blue = speaking)
- **Streaming text should appear character-by-character** (not word-by-word) — feels faster
- **Dark theme** by default — looks more "AI assistant", hides any rendering artifacts, easier on eyes in demo room lighting
- **Minimal chrome** — no menus, no settings panel during demo, full focus on the conversation
- **Large, readable font** for transcript — demos often happen with audience watching a screen

---

## 8. Failure Isolation Strategy

### 8.1 API Failure Handling

```python
# LLM fallback chain
try:
    async for token in groq_llm.stream(messages):
        yield token
except (GroqAPIError, httpx.TimeoutException, httpx.ConnectError) as e:
    logger.warning(f"Groq LLM failed: {e}. Falling back to Gemini.")
    async for token in gemini_llm.stream(messages):
        yield token
except Exception as e:
    logger.error(f"All LLM backends failed: {e}")
    yield "I'm having trouble connecting right now. Please try again in a moment."
```

```python
# STT failure handling
try:
    result = await groq_stt.transcribe(audio_bytes)
except Exception as e:
    logger.error(f"Groq STT failed: {e}")
    # Send status to client
    await websocket.send_json({"type": "status", "state": "error", 
                               "message": "Could not transcribe audio. Please try again."})
    return  # Do NOT crash the pipeline
```

```python
# TTS failure handling
try:
    wav = kokoro_en.synthesize(sentence)
except Exception as e:
    logger.error(f"Kokoro TTS failed: {e}")
    # Skip this sentence's audio — text is already streaming to UI
    # User sees the response even if audio fails
    continue
```

### 8.2 Fallback Behavior Matrix

| Failure | User Experience | Recovery |
|---|---|---|
| **Groq STT fails** | Status shows "couldn't hear you" | Retry on next utterance. Text input always available. |
| **Groq LLM fails** | Gemini responds instead | Transparent to user. Log for developer. |
| **Both LLMs fail** | Friendly "connection issue" spoken/shown | Displayed as assistant message. Never crashes. |
| **Kokoro TTS crash** | Text shown, no audio | Stream continues. User reads response. |
| **Tavily search fails** | Falls back to GENERAL intent | LLM answers from knowledge. Graceful. |
| **WebSocket disconnect** | Client shows "reconnecting..." | Auto-reconnect with exponential backoff (3 attempts). |
| **Microphone unavailable** | Text input mode activates | Always-visible text input. |
| **Network timeout** | Status indicator shows "connecting" | Retry with timeout message after 10s. |

### 8.3 Offline Degradation

If internet is unavailable (rare demo scenario):
- STT and LLM will fail → display user-friendly error in UI
- TTS (Kokoro) still works locally
- Text input still works but cannot get AI responses
- **Recommend: have a pre-prepared "offline mode" conversation history that can be replayed as a fallback demo**

### 8.4 Startup Health Checks

```python
# server/main.py lifespan
async def startup():
    # 1. Check API keys present in env
    assert os.getenv("GROQ_API_KEY"), "GROQ_API_KEY not set in .env"
    
    # 2. Warm up Kokoro TTS (prevent first-use lag)
    logger.info("Pre-warming Kokoro TTS...")
    kokoro_tts.synthesize("Hello.")  # ~2–5s first load
    
    # 3. Test Groq connectivity
    logger.info("Testing Groq API connectivity...")
    await groq_client.ping()  # Or a minimal test STT call
    
    # 4. Log readiness
    logger.info("✅ Server ready. Accepting connections.")
```

### 8.5 Logging Strategy

- **Default log level: INFO** — shows pipeline events without noise
- **DEBUG mode: `LOG_LEVEL=DEBUG` in .env** — shows full token streams, timing
- **Log format**: `[timestamp] [level] [component] message` — easy to read during live debugging
- **Critical: log TTFA per turn** — visible timing helps diagnose slow demos instantly
- **No sensitive data in logs**: do not log full conversation content at INFO level

### 8.6 "Never Looks Broken" Rules

1. The UI must always show a status indicator. If the pipeline is stuck, the user sees "thinking..." — not a blank screen.
2. All error messages must be friendly: "I'm having trouble connecting" not "GroqAPIError: 503 Service Unavailable".
3. Text input is always visible and functional. Even if voice fails, the demo continues.
4. The server must never crash on a single connection's error. All worker exceptions must be caught and logged.
5. If the client disconnects and reconnects, the server creates a fresh pipeline state. No stale locks.

---

## 9. Final Recommended Stack

```
┌──────────────────────────────────────────────────────────────────────┐
│                    FINAL DEMO STACK                                  │
├──────────────────────────────────────────────────────────────────────┤
│  Frontend:       React 18 + Vite + Tailwind CSS                      │
│                  Pre-built static files served by FastAPI            │
│                  WebSocket client via native browser WebSocket API   │
│                                                                      │
│  Backend:        FastAPI + uvicorn (Python 3.11)                     │
│                  asyncio pipeline (5 workers, same design as orig.)  │
│                  Single process, no subprocesses, no Docker          │
│                                                                      │
│  STT:            Groq Whisper API (whisper-large-v3-turbo)           │
│                  Python: groq SDK                                    │
│                                                                      │
│  LLM (primary):  Groq API — llama-3.3-70b-versatile                 │
│                  (or llama-3.1-8b-instant for faster/cheaper)        │
│                                                                      │
│  LLM (fallback): Google Gemini 2.0 Flash API                        │
│                  Python: google-generativeai SDK                     │
│                                                                      │
│  TTS:            Kokoro-82M (local, CPU)                             │
│                  EN: KPipeline(lang_code='a')                        │
│                  JA: KPipeline(lang_code='j')                        │
│                  Python: kokoro package                              │
│                                                                      │
│  Streaming:      asyncio.Queue pipeline + WebSocket binary frames    │
│                  Sentence-boundary TTS streaming (unchanged)         │
│                  LLM token streaming via Groq SSE                    │
│                                                                      │
│  Search:         Tavily API (optional, tavily-python)                │
│                  Simple keyword intent: regex check                  │
│                  Off by default; enable via TAVILY_API_KEY in .env   │
│                                                                      │
│  Deployment:     Native Python (no Docker)                           │
│                  Single-script launch (start.sh / start.bat)         │
│                  uv for package management + lockfile                │
│                                                                      │
│  Environment:    Python 3.11, single venv, no CUDA                  │
│                  Cross-platform: Linux / macOS / Windows             │
│                                                                      │
│  Containerization: None (intentionally removed for demo reliability) │
│                                                                      │
│  Optional local: Kokoro only (already included above)               │
│  Optional cloud: Tavily search (one API key, off by default)        │
│                                                                      │
│  Required API keys: GROQ_API_KEY (mandatory)                        │
│  Optional API keys: GEMINI_API_KEY, TAVILY_API_KEY                  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 10. Final Deliverable

### A. Executive Summary

**What it is:**  
A portable, API-powered voice assistant demo that achieves real-time voice-to-voice conversation in under 2.5 GB of disk space, with zero GPU requirements, on any machine with Python 3.11 and internet access. It preserves the full streaming pipeline architecture of the original (asyncio workers, sentence-boundary TTS, barge-in interrupts, conversational memory) while replacing every heavy local component with cloud APIs or minimal local alternatives.

**Why this architecture was chosen:**  
The original system's architecture is *correct and well-designed* — the asyncio pipeline, WebSocket transport, and sentence-boundary TTS streaming are exactly the right patterns for low-latency voice AI. The problem is entirely in the *infrastructure layer*: CUDA, Docker, multiple Python environments, 25+ GB of models, and machine-specific hardcoded paths. This plan surgically removes the infrastructure complexity while preserving the pipeline design. The result is a system that runs on a demo laptop in 10 minutes with no GPU, no Docker, and one mandatory API key.

**Why it is better for demos:**  
Three reasons. First, it actually *works* on client machines without a debugging session — the single biggest demo failure mode is eliminated. Second, Groq's LLaMA 3.3-70B produces markedly better conversation quality than the local 7B model, making the AI actually more impressive. Third, the estimated TTFA improves from 1.5–4s to 0.4–0.8s because there is no local GPU contention — the demo *feels* faster than the production system.

---

### B. Implementation Roadmap

#### Phase 1 — Working Voice Demo (Target: 1–2 days)
**Goal: End-to-end voice conversation works. Demo-able.**

1. Create new repo from scratch (do not fork original — fresh start avoids inheriting broken deps)
2. Set up Python 3.11 + uv environment with minimal requirements.txt
3. Port `server/pipeline.py` — keep the 5-worker asyncio architecture exactly
4. Replace `WhisperSTT` with `GroqSTTBackend` (Groq SDK, async upload + transcribe)
5. Replace `OllamaBackend` with `GroqLLMBackend` (Groq SDK, streaming)
6. Add `GeminiLLMBackend` as fallback (google-generativeai, streaming)
7. Replace `LLMFallbackChain` with simplified 2-backend chain
8. Port `KokoroTTS` and `KokoroJapaneseTTS` exactly from original (no changes needed)
9. Port `TTSRouter` without KokoClone path
10. Port `client/` audio stack (audio_capture, vad, ws_client, audio_playback) exactly
11. Port `server/lang/detector.py` — use Whisper language field from Groq response
12. Wire server startup with Kokoro pre-warm
13. **Test: end-to-end voice turn completes successfully**

Deliverable: `python server/main.py` + `python client/main.py` — voice conversation works.

#### Phase 2 — Browser UI + Polish (Target: 1 day)
**Goal: Looks impressive. Demo-ready UI.**

1. Build React UI: status indicator, transcript display, streaming LLM text, audio pulse animation, text input fallback
2. Pre-build with Vite, commit `ui/dist/` to repo
3. Mount static files in FastAPI: `app.mount("/", StaticFiles(directory="ui/dist"))`
4. Add WebSocket JSON message relay to browser (server broadcasts `llm_text_chunk`, `transcript`, `status` to a secondary HTTP/SSE endpoint or second WebSocket path for the browser)
5. Implement barge-in interrupt flow
6. Add startup health checks + Kokoro pre-warm
7. Write `start.sh` / `start.bat` launcher scripts
8. Write `setup.sh` one-time install script
9. Write `.env.example` with clear instructions
10. **Test: Full demo flow works. Status indicators transition correctly. Streaming text visible.**

Deliverable: `./start.sh` opens browser, demo runs end-to-end.

#### Phase 3 — Hardening + Optional Features (Target: 0.5–1 day)
**Goal: Stable for any demo. No edge-case crashes.**

1. Implement all fallback error handling (Section 8)
2. Add auto-reconnect in WebSocket client
3. Add Tavily web search (keyword intent check + Tavily API call) — behind `TAVILY_API_KEY` env flag
4. Add per-turn TTFA logging
5. Test on at least 3 different machines (Windows, macOS, Linux)
6. Document setup in README.md (10-minute setup guide)
7. Pin all deps + commit `uv.lock`
8. Optional: Japanese TTS with pyopenjtalk — document as optional install

Deliverable: Complete, stable, portable demo. README with setup instructions.

---

### C. Coding-Agent Handoff Notes

**Read this section carefully before writing any code.**

#### Implementation Priorities
1. **Get voice working first**. Do not build the UI until voice → text → LLM → TTS → audio works end-to-end. UI polish is Phase 2.
2. **Preserve the pipeline architecture**. The 5-worker asyncio queue design from the original is correct. Do not simplify it into a synchronous chain. Streaming requires the concurrency.
3. **Groq SDK first, not raw HTTP**. Use the official `groq` Python package for both STT and LLM. It handles auth, retries, and streaming correctly.
4. **Test Kokoro separately before integrating**. Run a standalone script that synthesizes 5–10 sentences and measures time. Confirm CPU inference latency is acceptable before wiring into the pipeline.

#### Architecture Constraints
- **Single process server**: all pipeline workers in one `uvicorn` process. No subprocess spawning.
- **No database**: conversation history is in-memory (`list` in `PipelineState`). Do not add SQLite, Redis, or any persistence.
- **No message broker**: asyncio queues only. No Celery, RabbitMQ, or Redis streams.
- **No extra microservices**: one server process, one client process. That's it.
- **WebSocket protocol**: preserve the exact message types from the original (`session_start`, `transcript`, `llm_text_chunk`, `status`, `interrupt`, binary audio). The browser UI will speak this protocol too.
- **Browser UI WebSocket**: add a second WebSocket endpoint (`/ws/ui`) for the browser. The browser does NOT send audio — it only receives JSON events. The Python client handles all audio I/O.

#### What NOT to Overengineer
- ❌ Do not add authentication, API rate limiting, or multi-user session management
- ❌ Do not add a database or persistent conversation logs
- ❌ Do not add configuration management beyond a simple `.env` file
- ❌ Do not add unit tests in Phase 1 (add integration tests in Phase 3 if time allows)
- ❌ Do not add Docker or docker-compose at any phase
- ❌ Do not add a plugin/extension architecture
- ❌ Do not support multiple simultaneous users (single connection per demo)
- ❌ Do not add streaming transcription (send complete utterances after VAD, not incremental frames)
- ❌ Do not add TTS caching or audio compression — WAV is fine for a demo

#### What to Keep Modular
- ✅ **STT backends**: define a `BaseSTTBackend` protocol with `async def transcribe(audio_bytes: bytes) -> TranscriptionResult`. Keep `GroqSTTBackend` behind this interface so a local Whisper backend could be swapped in later.
- ✅ **LLM backends**: define a `BaseLLMBackend` protocol with `async def stream(messages: list) -> AsyncIterator[str]`. Keep `GroqLLMBackend` and `GeminLLMBackend` behind this interface.
- ✅ **TTS backends**: keep the `TTSRouter` abstraction. `KokoroTTS` and `KokoroJapaneseTTS` are separate classes.
- ✅ **Config**: keep all settings in a `Config` dataclass loaded from `.env` via `python-dotenv`. No hardcoded values.
- ✅ **Search**: keep the search call behind a `USE_SEARCH` flag in config. If `TAVILY_API_KEY` is unset, skip entirely.

#### Expected Code Organization
```
demo-voice-assistant/
├── server/
│   ├── main.py              ← FastAPI app, lifespan, WS endpoints (/ws, /ws/ui)
│   ├── pipeline.py          ← VoicePipeline, PipelineState, 5 workers
│   ├── config.py            ← Config dataclass, loads .env
│   ├── stt/
│   │   ├── base.py          ← BaseSTTBackend protocol
│   │   └── groq_stt.py      ← GroqSTTBackend
│   ├── llm/
│   │   ├── base.py          ← BaseLLMBackend protocol
│   │   ├── groq_llm.py      ← GroqLLMBackend
│   │   ├── gemini_llm.py    ← GeminiLLMBackend
│   │   ├── chain.py         ← LLMChain (Groq → Gemini fallback)
│   │   ├── intent.py        ← Keyword intent classifier (simple regex)
│   │   └── prompt_builder.py ← System prompt + message assembly
│   ├── tts/
│   │   ├── tts_router.py    ← TTSRouter (EN/JA)
│   │   └── kokoro_tts.py    ← KokoroTTS + KokoroJapaneseTTS
│   ├── search/
│   │   └── tavily_search.py ← TavilySearchClient (optional)
│   └── lang/
│       └── detector.py      ← Language detection from Groq STT response
├── client/
│   ├── main.py
│   ├── audio_capture.py
│   ├── vad.py
│   ├── ws_client.py
│   └── audio_playback.py
├── ui/
│   ├── dist/                ← Pre-built static files (committed)
│   └── src/                 ← React source (for development)
├── setup.sh
├── start.sh
├── start.bat
├── requirements.txt
├── uv.lock
├── .env.example
└── README.md
```

#### Key Implementation Notes for Specific Components

**`GroqSTTBackend`**:
```python
# Groq STT expects a file-like object, not raw bytes
# Wrap PCM16 bytes in a WAV header before sending
import io, wave
async def transcribe(self, pcm16_bytes: bytes) -> TranscriptionResult:
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(16000)
        wf.writeframes(pcm16_bytes)
    wav_buffer.seek(0)
    wav_buffer.name = "audio.wav"  # Groq SDK needs a name attribute
    result = await self.client.audio.transcriptions.create(
        model="whisper-large-v3-turbo",
        file=wav_buffer,
        response_format="verbose_json"  # gives language + duration
    )
    return TranscriptionResult(text=result.text, language=result.language)
```

**Kokoro pre-warm in lifespan:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm: lazy load triggers on first synthesis
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: tts_router.synthesize_stream("Hello.", "en"))
    logger.info("✅ Kokoro pre-warmed. Server ready.")
    yield
```

**Browser WebSocket (`/ws/ui`):**  
The browser only needs to receive events. Keep it simple: a separate set of connected browser WebSocket clients stored in a `Set`. Broadcast JSON events to all connected browser clients from the pipeline workers. The browser never sends audio.

**VAD silence threshold:**  
Set to `600ms` (down from original 800ms). Makes demo feel more responsive. Configurable via `VAD_SILENCE_MS` env var.

**Gemini streaming:**  
The `google-generativeai` SDK streams differently from Groq. Use `generate_content_async(..., stream=True)` and iterate `response.text` chunks. Wrap in the same `BaseLLMBackend` interface.

---

*End of Lightweight Demo Architecture Blueprint*  
*May 10, 2026*
