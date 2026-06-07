"""
server/api — REST endpoint package for building-nav integration.

Exports a single ``api_router`` (prefix ``/api``) that is registered on the
main FastAPI application in ``server/main.py``.
"""

from fastapi import APIRouter

from server.api import detect_language, health, navigate, stt, tts

api_router = APIRouter(prefix="/api")
api_router.include_router(stt.router)
api_router.include_router(tts.router)
api_router.include_router(detect_language.router)
api_router.include_router(navigate.router)
api_router.include_router(health.router)
