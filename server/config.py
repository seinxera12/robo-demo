"""
Configuration management for the Lightweight Voice Demo server.

All settings are loaded from a .env file (or environment variables) at startup.
No API keys, model names, or host/port values are hardcoded outside this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Config:
    groq_api_key: str           # Required. No default.
    gemini_api_key: str = ""    # Optional fallback LLM
    tavily_api_key: str = ""    # Optional web search
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_llm_model: str = "llama-3.3-70b-versatile"
    vad_silence_ms: int = 600
    log_level: str = "INFO"
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    ws_port: int = 8000

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        return cls(
            groq_api_key=os.environ["GROQ_API_KEY"],
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            groq_stt_model=os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
            groq_llm_model=os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
            vad_silence_ms=int(os.getenv("VAD_SILENCE_MS", "600")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            server_host=os.getenv("SERVER_HOST", "0.0.0.0"),
            server_port=int(os.getenv("SERVER_PORT", "8000")),
            ws_port=int(os.getenv("WS_PORT", "8000")),
        )
