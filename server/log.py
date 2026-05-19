"""
Structured logging for the Lightweight Voice Demo.

Two tiers:

  system.log   — high-level pipeline flow (one line per meaningful event)
                 Stages: SERVER, WS, SESSION, STATE, ROBO, STT, LLM, TTS,
                         AUDIO_IN, AUDIO_OUT, SEARCH

  stt.log      — detailed STT events (API call, bytes, latency, transcript)
  llm.log      — detailed LLM events (prompt, token stream, latency)
  tts.log      — detailed TTS events (synthesis, WAV bytes, playback timing)
  search.log   — detailed search events (query, results, latency)

Usage
-----
High-level (system.log + console):
    from server.log import pipeline_event, pipeline_warn, pipeline_error, pipeline_separator

Low-level detail (dedicated file only, not in system.log):
    from server.log import stt_log, llm_log, tts_log, search_log
    stt_log.info("transcribed %d bytes in %dms: %r", n, ms, text)
    tts_log.debug("synthesised %d WAV bytes for %r", n, sentence)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logging")
_FMT     = "%(asctime)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_file_handler(filename: str, level: int = logging.DEBUG) -> RotatingFileHandler:
    os.makedirs(_LOG_DIR, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(_LOG_DIR, filename),
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    return fh


def _make_logger(name: str, filename: str, console: bool = False) -> logging.Logger:
    log = logging.getLogger(f"demo.{name}")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(_make_file_handler(filename))
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        log.addHandler(ch)
    return log


# ---------------------------------------------------------------------------
# Loggers
# ---------------------------------------------------------------------------

# system.log — high-level, also printed to console
_system = _make_logger("system", "system.log", console=True)

# Dedicated detail logs — file only
stt_log    = _make_logger("stt",    "stt.log")
llm_log    = _make_logger("llm",    "llm.log")
tts_log    = _make_logger("tts",    "tts.log")
search_log = _make_logger("search", "search.log")

# ---------------------------------------------------------------------------
# system.log helpers
# ---------------------------------------------------------------------------

def _fmt_kwargs(**kwargs: Any) -> str:
    parts = []
    for k, v in kwargs.items():
        if isinstance(v, str) and (" " in v or not v):
            parts.append(f'{k}="{v}"')
        else:
            parts.append(f"{k}={v}")
    return "  ".join(parts)


def _msg(stage: str, event: str, **kwargs: Any) -> str:
    detail = _fmt_kwargs(**kwargs)
    return f"{stage:<10} | {event:<20} | {detail}"


def pipeline_event(stage: str, event: str, **kwargs: Any) -> None:
    """Normal pipeline flow — INFO in system.log + console."""
    _system.info(_msg(stage, event, **kwargs))


def pipeline_warn(stage: str, event: str, **kwargs: Any) -> None:
    """Recoverable issue — WARNING in system.log + console."""
    _system.warning(_msg(stage, event, **kwargs))


def pipeline_error(stage: str, event: str, **kwargs: Any) -> None:
    """Pipeline error — ERROR in system.log + console."""
    _system.error(_msg(stage, event, **kwargs))


def pipeline_separator(label: str = "") -> None:
    """Visual separator in system.log."""
    line = "─" * 72
    if label:
        pad = max(0, (72 - len(label) - 2) // 2)
        line = "─" * pad + f" {label} " + "─" * pad
    _system.info(line)
