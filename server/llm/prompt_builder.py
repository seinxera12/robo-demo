"""
PromptBuilder — assembles the messages list for the LLM API.

.. deprecated::
    This module has been superseded by ``server/llm/assembler.py``
    (``PromptAssembler``).  It is kept here during the transition period
    so that any remaining import sites continue to work.  New code should
    use ``PromptAssembler`` instead.

Combines the system prompt, optional Tavily search context, conversation
history (up to 10 turns / ~3000 token budget), and the current user
transcript into an OpenAI-compatible message list.
Requirements: 3.2
"""

from __future__ import annotations

_SYSTEM_PROMPT = (
    "You are a helpful, friendly voice assistant. "
    "Keep your answers concise and conversational — you are speaking aloud, "
    "so avoid markdown, bullet points, or long lists. "
    "Respond naturally as if talking to a person face-to-face."
)

_MAX_HISTORY_TURNS = 10  # Maximum number of user/assistant pairs to include


class PromptBuilder:
    """
    Builds the messages list sent to the LLM on each turn.

    .. deprecated::
        ``PromptBuilder`` has been replaced by ``PromptAssembler`` in
        ``server/llm/assembler.py``, which supports modular block assembly,
        deployment configuration, multi-route context injection, and
        session-memory management.

        This class is retained only for backward compatibility during the
        transition period.  New code should use ``PromptAssembler`` instead.
        ``PromptBuilder`` will be removed in a future cleanup pass once all
        call sites have been migrated.
    """

    def build(
        self,
        transcript: str,
        history: list[dict],
        search_context: str = "",
    ) -> list[dict]:
        """
        Assemble an OpenAI-compatible messages list.

        The resulting list has the following structure:
        1. System prompt (always present).
        2. Search context message (only when ``search_context`` is non-empty).
        3. Conversation history (up to ``_MAX_HISTORY_TURNS`` user/assistant pairs).
        4. Current user transcript as the final ``user`` message.

        Args:
            transcript:     The user's current spoken/typed input.
            history:        Conversation history as a list of
                            ``{"role": "user"|"assistant", "content": str}`` dicts.
                            Each *turn* is one user message + one assistant message.
            search_context: Optional Tavily search results to prepend as context.

        Returns:
            List of ``{"role": ..., "content": ...}`` dicts ready for the LLM API.
        """
        messages: list[dict] = []

        # 1. System prompt
        messages.append({"role": "system", "content": _SYSTEM_PROMPT})

        # 2. Optional search context — prepended as a system message so the
        #    model treats it as background knowledge for this turn only.
        if search_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following information was retrieved from the web "
                        "and may be relevant to the user's question:\n\n"
                        + search_context
                    ),
                }
            )

        # 3. Conversation history — enforce the sliding window.
        #    Each turn = 1 user message + 1 assistant message (2 entries).
        max_entries = _MAX_HISTORY_TURNS * 2
        trimmed_history = history[-max_entries:] if len(history) > max_entries else history
        messages.extend(trimmed_history)

        # 4. Current user transcript
        messages.append({"role": "user", "content": transcript})

        return messages
