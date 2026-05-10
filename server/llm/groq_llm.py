"""
Groq LLM backend implementation.

Uses the Groq Python SDK with streaming to yield tokens from LLaMA models.
Requirements: 1.3
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import groq

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqLLMBackend:
    """LLM backend that streams tokens from Groq's LLaMA API."""

    def __init__(self, client: groq.AsyncGroq, model: str = _DEFAULT_MODEL) -> None:
        """
        Args:
            client: An initialised `groq.AsyncGroq` client.
            model:  Groq model name. Defaults to ``llama-3.3-70b-versatile``.
        """
        self.client = client
        self.model = model

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """
        Stream tokens from the Groq LLM.

        Args:
            messages: OpenAI-compatible message list.

        Yields:
            Individual token strings from the streaming response.
        """
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content
