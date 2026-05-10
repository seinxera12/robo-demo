"""
Intent classifier for the Lightweight Voice Demo.

Classifies a transcript as SEARCH or GENERAL intent using keyword regex patterns.
The caller is responsible for only invoking this when config.tavily_api_key is non-empty.
"""

from __future__ import annotations

import re

# Patterns that indicate a web search is likely useful
_SEARCH_PATTERNS = re.compile(
    r"\b(search for|what is|latest|today|current|news)\b",
    re.IGNORECASE,
)


class IntentClassifier:
    """Keyword-regex intent classifier.

    Returns ``"SEARCH"`` when the transcript matches any of the configured
    keyword patterns, and ``"GENERAL"`` otherwise.
    """

    def classify(self, transcript: str) -> str:
        """Classify *transcript* as ``"SEARCH"`` or ``"GENERAL"``.

        Args:
            transcript: The transcribed user utterance.

        Returns:
            ``"SEARCH"`` if any keyword pattern matches, ``"GENERAL"`` otherwise.
        """
        if _SEARCH_PATTERNS.search(transcript):
            return "SEARCH"
        return "GENERAL"
