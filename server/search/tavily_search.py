"""
Tavily web search client for the Lightweight Voice Demo.
"""

from __future__ import annotations

import time

from server.log import search_log


class TavilySearchClient:
    """Async-friendly wrapper around the Tavily search SDK."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TavilySearchClient requires a non-empty api_key.")
        from tavily import TavilyClient
        self._client = TavilyClient(api_key=api_key)

    async def search(self, query: str) -> str:
        """Search Tavily for *query* and return formatted results."""
        search_log.info("search_start  query=%r", query[:120])
        t0 = time.monotonic()
        try:
            response = self._client.search(query)
            results = response.get("results", [])
            parts: list[str] = []
            for result in results:
                title   = result.get("title",   "").strip()
                content = result.get("content", "").strip()
                if title and content:
                    parts.append(f"{title}: {content}")
                elif content:
                    parts.append(content)
                elif title:
                    parts.append(title)
            context = "\n".join(parts)
            ms = int((time.monotonic() - t0) * 1000)
            search_log.info(
                "search_done  results=%d  context_chars=%d  latency_ms=%d",
                len(results), len(context), ms,
            )
            return context
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            search_log.error("search_failed  query=%r  latency_ms=%d  error=%s",
                             query[:120], ms, exc)
            return ""
