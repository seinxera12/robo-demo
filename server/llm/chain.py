"""
LLMChain — two-tier fallback LLM backend.

Tries the primary backend (Groq) first; on network/API errors falls back to
the secondary backend (Gemini). If both fail, yields a hardcoded error message.
Requirements: 1.3, 1.4, 1.5
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from groq import APIError as GroqAPIError

from server.llm.base import BaseLLMBackend

logger = logging.getLogger(__name__)

_FALLBACK_MESSAGE = (
    "I'm having trouble connecting right now. Please try again in a moment."
)


class LLMChain:
    """
    Two-tier LLM chain with automatic fallback.

    Streams tokens from ``primary``; if that raises a Groq API error or an
    httpx network error, transparently falls back to ``fallback``.  If both
    backends fail, yields a single hardcoded error message string.
    """

    def __init__(self, primary: BaseLLMBackend, fallback: BaseLLMBackend) -> None:
        """
        Args:
            primary:  The preferred LLM backend (typically Groq).
            fallback: The backup LLM backend (typically Gemini).
        """
        self.primary = primary
        self.fallback = fallback

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """
        Stream tokens, falling back gracefully on errors.

        Args:
            messages: OpenAI-compatible message list.

        Yields:
            Individual token strings from whichever backend succeeds,
            or the hardcoded error message if both fail.
        """
        try:
            async for token in self.primary.stream(messages):
                yield token
        except (GroqAPIError, httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"Groq LLM failed: {e}. Falling back to Gemini.")
            try:
                async for token in self.fallback.stream(messages):
                    yield token
            except Exception as e:
                logger.error(f"All LLM backends failed: {e}")
                yield _FALLBACK_MESSAGE
        except Exception as e:
            logger.error(f"All LLM backends failed: {e}")
            yield _FALLBACK_MESSAGE
