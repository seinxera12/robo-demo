"""
GET /api/health — Lightweight health check for the building-nav circuit breaker.

Returns the current readiness state of the TTS and STT backends without
performing any blocking I/O or external API calls.  Response time target: <200 ms.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    tts_ready: bool
    stt_ready: bool


@router.get("/health", response_model=HealthResponse)
async def health_endpoint(request: Request) -> HealthResponse:
    """Return the current health status of Robo's AI backends.

    - ``tts_ready``: True when at least one Kokoro TTS engine warm-up
      completed successfully (``app.state.tts_warmed_up``).
    - ``stt_ready``: True when the GroqSTTBackend is initialised and a
      non-empty Groq API key is configured.
    - Always returns HTTP 200 — even when components are not ready —
      so the caller can act on the boolean flags rather than an error status.
    - No blocking I/O; reads only ``app.state`` flags set during lifespan startup.
    """
    tts_ready: bool = getattr(request.app.state, "tts_warmed_up", False)

    stt_ready: bool = (
        hasattr(request.app.state, "stt_backend")
        and bool(getattr(getattr(request.app.state, "config", None), "groq_api_key", ""))
    )

    return HealthResponse(status="ok", tts_ready=tts_ready, stt_ready=stt_ready)
