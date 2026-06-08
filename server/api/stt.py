"""
POST /api/stt — Speech-to-text endpoint for the building-nav integration.

Accepts a multipart/form-data audio upload (WAV or PCM16) and returns the
transcribed text and detected language via the existing GroqSTTBackend.

Privacy: audio bytes and transcribed text are never logged at INFO level.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from server.lang.detector import normalise_language

logger = logging.getLogger(__name__)

router = APIRouter()


class STTResponse(BaseModel):
    text: str
    language: str


@router.post("/stt", response_model=STTResponse)
async def stt_endpoint(
    request: Request,
    file: UploadFile = File(...),
) -> STTResponse:
    """Transcribe uploaded audio and return text + language.

    - Accepts ``multipart/form-data`` with field name ``file`` (WAV or PCM16).
    - Returns ``{"text": "...", "language": "en"}`` on success.
    - Returns HTTP 422 if the file is absent or empty.
    - Returns HTTP 502 if the Groq STT backend raises an exception.
    """
    t0 = time.monotonic()

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="Audio file is empty or missing.")

    try:
        result = await request.app.state.stt_backend.transcribe(audio_bytes)
    except Exception as exc:
        logger.debug("stt_failed  detail=%s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "stt_failed", "detail": str(exc)},
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    normalised_lang = normalise_language(result.language)
    # Log metadata only — never log transcribed text content (privacy/FR-017)
    logger.info(
        "stt  status=200  latency_ms=%d  language=%s",
        latency_ms,
        normalised_lang,
    )

    return STTResponse(text=result.text, language=normalised_lang)
