"""
POST /api/tts — Text-to-speech endpoint for the building-nav integration.

Accepts JSON ``{"text": "...", "language": "en|ja|zh|ko"}`` and returns
synthesised 24 kHz WAV bytes via the existing TTSRouter.

Korean (``"ko"``) is not supported by Kokoro — the endpoint returns HTTP 406
with ``{"fallback": "browser_tts", "language": "ko"}`` so the client can
fall back to the Web Speech API.

Privacy: request text is never logged at INFO level.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter()

_ACCEPTED_LANGUAGES = frozenset({"en", "ja", "zh", "ko"})


class TTSRequest(BaseModel):
    text: str
    language: str

    @field_validator("text")
    @classmethod
    def text_not_empty_or_whitespace(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must be a non-empty, non-whitespace string")
        return v

    @field_validator("language")
    @classmethod
    def language_must_be_accepted(cls, v: str) -> str:
        if v not in _ACCEPTED_LANGUAGES:
            raise ValueError(
                f"language must be one of {sorted(_ACCEPTED_LANGUAGES)}, got {v!r}"
            )
        return v


@router.post("/tts")
async def tts_endpoint(request: Request, body: TTSRequest) -> Response:
    """Synthesise text and return WAV audio bytes.

    - Returns ``audio/wav`` bytes with HTTP 200 for ``language`` in ``{"en","ja","zh"}``.
    - Returns HTTP 406 JSON for ``language == "ko"`` (browser TTS fallback).
    - Returns HTTP 422 for missing/empty text or unsupported language (Pydantic).
    - Returns HTTP 502 if ``TTSRouter.synthesize()`` raises an exception.
    """
    # Korean: no Kokoro support — signal browser-side fallback
    if body.language == "ko":
        return JSONResponse(
            status_code=406,
            content={"fallback": "browser_tts", "language": "ko"},
        )

    t0 = time.monotonic()
    try:
        wav_bytes = await request.app.state.tts_router.synthesize(body.text, body.language)
    except Exception as exc:
        logger.debug("tts_failed  detail=%s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "tts_failed", "detail": str(exc)},
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    # Log metadata only — never log the text content (privacy/FR-017)
    logger.info(
        "tts  status=200  latency_ms=%d  language=%s  wav_bytes=%d",
        latency_ms,
        body.language,
        len(wav_bytes),
    )

    return Response(content=wav_bytes, media_type="audio/wav")
