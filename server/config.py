"""
Configuration management for the Lightweight Voice Demo server.

All settings are loaded from a .env file (or environment variables) at startup.
No API keys, model names, or host/port values are hardcoded outside this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
import sys

from dotenv import load_dotenv


def get_base_path() -> str:
    """
    Returns the base directory of the application.
    When running as a PyInstaller --onedir bundle: sys._MEIPASS
    (_internal/ folder where all bundled files land).
    When running as a normal Python script: the project root.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle — all bundled files live under _MEIPASS
        # (_internal/ in --onedir mode).
        return sys._MEIPASS
    else:
        # Running as normal Python script
        # __file__ = <project_root>/server/config.py
        # two dirname() calls walk up to the project root
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_config_path() -> str:
    """
    Returns the path to the .env config file.

    When running as a PyInstaller bundle: uses AppData\\Roaming\\DemoVoiceAssistant\\.env
    so the file lives in a user-writable location (C:\\Program Files is read-only).

    When running as a normal Python script: uses the project root .env
    so existing dev workflow is unchanged.
    """
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        config_dir = os.path.join(appdata, 'DemoVoiceAssistant')
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, '.env')
    else:
        return os.path.join(get_base_path(), '.env')


BASE_PATH = get_base_path()
CONFIG_PATH = get_config_path()

MODELS_DIR = os.path.join(BASE_PATH, "models")
KOKORO_MODEL_DIR = os.path.join(MODELS_DIR, "kokoro")

# Derived paths used by server components — all relative to BASE_PATH (read-only assets)
UI_DIST_DIR = os.path.join(BASE_PATH, "ui", "dist")
PROMPTS_DIR = os.path.join(BASE_PATH, "server", "prompts")
CONFIG_DIR  = os.path.join(BASE_PATH, "config")
DEPLOYMENT_YAML = os.path.join(BASE_PATH, "config", "deployment.yaml")

# ── Write-safe directories (APPDATA in frozen, project root in dev) ───────────
if getattr(sys, 'frozen', False):
    _WRITE_BASE = os.path.join(
        os.environ.get('APPDATA', os.path.expanduser('~')),
        'DemoVoiceAssistant'
    )
else:
    _WRITE_BASE = BASE_PATH

LOG_DIR   = os.path.join(_WRITE_BASE, 'logs')
DATA_DIR  = _WRITE_BASE
CACHE_DIR = os.path.join(_WRITE_BASE, 'cache')
TEMP_DIR  = os.path.join(os.environ.get('TEMP', _WRITE_BASE), 'DemoVoiceAssistant')

# Create all write directories on import — safe to call multiple times
for _d in (LOG_DIR, DATA_DIR, CACHE_DIR, TEMP_DIR):
    os.makedirs(_d, exist_ok=True)


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
        # Load from the resolved config path (AppData in frozen, project root in dev)
        load_dotenv(dotenv_path=CONFIG_PATH, override=False)
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
