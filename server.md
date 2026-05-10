# Server Setup

## Prerequisites

- Python 3.11
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager
- A Groq API key (free at [console.groq.com](https://console.groq.com))

## First-time setup

```bash
bash setup.sh
```

This creates the virtual environment, installs all dependencies, and downloads the Kokoro TTS model (~500 MB).

## Environment variables

Copy `.env.example` to `.env` and set your keys:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | **Yes** | Groq API key — used for Whisper STT and LLaMA LLM |
| `GEMINI_API_KEY` | No | Gemini API key — fallback LLM if Groq fails |
| `TAVILY_API_KEY` | No | Tavily API key — enables live web search |
| `GROQ_STT_MODEL` | No | STT model (default: `whisper-large-v3-turbo`) |
| `GROQ_LLM_MODEL` | No | LLM model (default: `llama-3.3-70b-versatile`) |
| `VAD_SILENCE_MS` | No | Silence duration before speech ends in ms (default: `600`) |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |
| `SERVER_PORT` | No | HTTP server port (default: `8000`) |
| `WS_PORT` | No | AudioClient WebSocket port (default: `8765`) |

## Start

**Linux / macOS:**
```bash
bash start.sh
```

**Windows:**
```bat
start.bat
```

This launches the server, audio client, and opens the browser at `http://localhost:8000`.

## Manual start (server only)

```bash
venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Audio client separately:
```bash
venv/bin/python -m client.main
```
