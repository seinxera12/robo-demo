"""
Groq LLM backend — streams tokens from LLaMA models via the Groq SDK.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import groq

from server.log import llm_log

_DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqLLMBackend:
    """LLM backend that streams tokens from Groq's LLaMA API."""

    def __init__(self, client: groq.AsyncGroq, model: str = _DEFAULT_MODEL) -> None:
        self.client = client
        self.model = model

    async def stream(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from the Groq LLM."""
        prompt_turns = sum(1 for m in messages if m["role"] == "user")
        llm_log.info("stream_start  model=%s  messages=%d  user_turns=%d",
                     self.model, len(messages), prompt_turns)
        t0 = time.monotonic()
        token_count = 0
        first_token_ms: int | None = None

        # Build kwargs for API call
        kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                if first_token_ms is None:
                    first_token_ms = int((time.monotonic() - t0) * 1000)
                    llm_log.info("first_token  latency_ms=%d", first_token_ms)
                token_count += 1
                yield content

        total_ms = int((time.monotonic() - t0) * 1000)
        llm_log.info("stream_done  tokens=%d  total_ms=%d", token_count, total_ms)
