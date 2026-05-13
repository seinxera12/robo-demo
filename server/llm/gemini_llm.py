"""
Gemini LLM backend implementation.

Uses the google-generativeai SDK with streaming to yield tokens from Gemini models.
Converts OpenAI-style messages to the Gemini format before sending.
Requirements: 1.4
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import google.generativeai as genai

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiLLMBackend:
    """LLM backend that streams tokens from Google's Gemini API."""

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        """
        Args:
            api_key: Gemini API key.
            model:   Gemini model name. Defaults to ``gemini-2.0-flash``.
        """
        genai.configure(api_key=api_key)
        self.model_name = model

    async def stream(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream tokens from the Gemini LLM.

        Converts OpenAI-style messages to Gemini format:
        - ``system`` role → ``system_instruction`` on the model
        - ``user`` role → ``user`` part
        - ``assistant`` role → ``model`` part

        Args:
            messages: OpenAI-compatible message list.
            max_tokens: Optional maximum number of tokens to generate.
            temperature: Optional sampling temperature (0.0 to 2.0).

        Yields:
            Individual token strings from the streaming response.
        """
        system_instruction: str | None = None
        history: list[dict] = []
        last_user_message: str = ""

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                # Accumulate system instructions (there may be more than one)
                if system_instruction is None:
                    system_instruction = content
                else:
                    system_instruction += "\n\n" + content
            elif role == "user":
                last_user_message = content
                history.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                history.append({"role": "model", "parts": [content]})

        # The last user message is the current turn; history excludes it
        # (GenerativeModel.start_chat takes prior turns, then send_message for current)
        chat_history = history[:-1] if history and history[-1]["role"] == "user" else history

        model_kwargs: dict = {}
        if system_instruction:
            model_kwargs["system_instruction"] = system_instruction

        # Build generation config for parameters
        generation_config = {}
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens
        if temperature is not None:
            generation_config["temperature"] = temperature
        if generation_config:
            model_kwargs["generation_config"] = generation_config

        model = genai.GenerativeModel(self.model_name, **model_kwargs)
        chat = model.start_chat(history=chat_history)

        response = await chat.send_message_async(last_user_message, stream=True)
        async for chunk in response:
            text = chunk.text
            if text:
                yield text
