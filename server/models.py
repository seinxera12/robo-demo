"""
Data models for the Lightweight Voice Demo server.

Contains the TranscriptionResult dataclass and all WebSocket message TypedDicts
used for communication between the server, AudioClient, and BrowserUI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal, Optional
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------


@dataclass
class TranscriptionResult:
    """Result from the STT backend after transcribing a PCM16 audio buffer."""

    text: str           # Transcribed speech text
    language: str       # ISO 639-1 code: "en", "ja", etc.
    duration: float = 0.0  # Audio duration in seconds (from Groq verbose_json)


# ---------------------------------------------------------------------------
# WebSocket message TypedDicts
# All JSON messages share a `type` discriminator field.
# ---------------------------------------------------------------------------


class StatusMessage(TypedDict):
    """Server → AudioClient + BrowserUI: current pipeline state."""

    type: Literal["status"]
    state: str          # "listening" | "thinking" | "speaking" | "error"
    message: Optional[str]  # Optional human-readable description


class SessionStartMessage(TypedDict):
    """Server → BrowserUI: sent once on WebSocket connect."""

    type: Literal["session_start"]
    session_id: str


class TranscriptMessage(TypedDict):
    """Server → BrowserUI: final transcription of a user utterance."""

    type: Literal["transcript"]
    text: str
    language: str


class LLMTextChunkMessage(TypedDict):
    """Server → BrowserUI: streaming token from the LLM."""

    type: Literal["llm_text_chunk"]
    text: str


class InterruptMessage(TypedDict):
    """AudioClient → Server: barge-in interrupt signal."""

    type: Literal["interrupt"]


class TextInputMessage(TypedDict):
    """BrowserUI → Server: text submitted via the UI text input fallback."""

    type: Literal["text_input"]
    text: str
