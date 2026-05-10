"""
Base protocol for LLM backends.

Defines the `BaseLLMBackend` Protocol that all LLM implementations must satisfy.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol


class BaseLLMBackend(Protocol):
    """Protocol for streaming LLM backends."""

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """
        Stream tokens from the LLM given a list of OpenAI-compatible messages.

        Args:
            messages: List of message dicts with "role" and "content" keys.

        Yields:
            Individual token strings as they arrive from the model.
        """
        ...
