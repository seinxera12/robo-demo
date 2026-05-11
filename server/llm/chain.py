"""
LLMChain — two-tier fallback LLM backend.

Tries the primary backend (Groq) first; on network/API errors falls back to
the secondary backend (Gemini). If both fail, yields a hardcoded error message.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from groq import APIError as GroqAPIError

from server.llm.base import BaseLLMBackend
from server.log import llm_log

logger = logging.getLogger(__name__)

_FALLBACK_MESSAGE = (
    "I'm having trouble connecting right now. Please try again in a moment."
)


class LLMChain:
    """Two-tier LLM chain with automatic fallback."""

    def __init__(self, primary: BaseLLMBackend, fallback: BaseLLMBackend) -> None:
        self.primary = primary
        self.fallback = fallback

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Stream tokens, falling back gracefully on errors."""
        try:
            async for token in self.primary.stream(messages):
                yield token
        except (GroqAPIError, httpx.TimeoutException, httpx.ConnectError) as e:
            llm_log.warning("primary_failed  error=%s  trying_fallback=true", e)
            logger.warning("Groq LLM failed: %s. Falling back to Gemini.", e)
            try:
                async for token in self.fallback.stream(messages):
                    yield token
            except Exception as e:
                llm_log.error("fallback_failed  error=%s", e)
                logger.error("All LLM backends failed: %s", e)
                yield _FALLBACK_MESSAGE
        except Exception as e:
            llm_log.error("stream_error  error=%s", e)
            logger.error("All LLM backends failed: %s", e)
            yield _FALLBACK_MESSAGE
