"""
Tavily web search client for the Lightweight Voice Demo.

Wraps the ``tavily-python`` SDK and returns formatted search results as a
plain string that can be prepended to the LLM message context.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TavilySearchClient:
    """Async-friendly wrapper around the Tavily search SDK.

    Only instantiate this class when ``api_key`` is non-empty.  The caller
    (``llm_worker`` in ``pipeline.py``) is responsible for that guard.

    Args:
        api_key: A valid Tavily API key.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TavilySearchClient requires a non-empty api_key.")
        from tavily import TavilyClient  # imported lazily to avoid hard dependency at import time

        self._client = TavilyClient(api_key=api_key)

    async def search(self, query: str) -> str:
        """Search Tavily for *query* and return formatted results.

        The results are formatted as a plain string with each result's title
        and content joined together, suitable for prepending to an LLM prompt.

        On any failure the method logs a warning and returns an empty string so
        that the pipeline can continue as ``GENERAL`` intent.

        Args:
            query: The search query derived from the user transcript.

        Returns:
            A formatted string of search results, or ``""`` on failure.
        """
        try:
            response = self._client.search(query)
            results = response.get("results", [])
            parts: list[str] = []
            for result in results:
                title = result.get("title", "").strip()
                content = result.get("content", "").strip()
                if title and content:
                    parts.append(f"{title}: {content}")
                elif content:
                    parts.append(content)
                elif title:
                    parts.append(title)
            return "\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily search failed for query %r: %s", query, exc)
            return ""
