"""
Router module for the Robo LLM pipeline.

This module provides the RouteResult dataclass and Router class for mapping
IntentResult to response routes and retrieving necessary context.
"""

from dataclasses import dataclass
from server.llm.assembler import DeploymentConfig
from server.search.tavily_search import TavilySearchClient


@dataclass
class RouteResult:
    """
    Output from the Router containing the selected route and retrieved context.
    
    Attributes:
        route_type: The selected route - one of "general", "environment", 
                   "web_search", "out_of_scope", or "clarify"
        retrieved_context: RAG chunks or search results (empty string if not applicable)
        direct_response: Set for OOS/low-confidence clarify routes that skip Call 2.
                        None for routes that need Call 2.
        clarification_suffix: Appended to Call 2 response for partial clarification
                             (confidence 0.3-0.45). None otherwise.
    """
    
    route_type: str
    retrieved_context: str
    direct_response: str | None
    clarification_suffix: str | None


class Router:
    """
    Maps IntentResult to response routes and retrieves necessary context.
    
    This class handles routing decisions based on intent classification results,
    retrieves context for environment and web search routes, and provides
    direct responses for out-of-scope queries.
    """
    
    # Clarification templates by language
    CLARIFICATION_TEMPLATES = {
        "ja": {
            "low_confidence": "申し訳ありませんが、よく理解できませんでした。もう一度お願いできますか？",
            "partial_match": "もし違っていたら、もう一度教えてください。",
        },
        "en": {
            "low_confidence": "I'm sorry, I didn't quite understand that. Could you please rephrase?",
            "partial_match": "If I misunderstood, please let me know.",
        },
    }
    
    def __init__(
        self,
        deployment_config: DeploymentConfig,
        tavily_client: TavilySearchClient | None,
    ) -> None:
        """
        Initialize the Router with deployment configuration and search client.
        
        Args:
            deployment_config: Deployment configuration for this environment
            tavily_client: Optional Tavily search client for web search routes
        """
        self.deployment_config = deployment_config
        self.tavily_client = tavily_client
    
    async def route(
        self,
        intent_result,  # IntentResult type (avoiding circular import)
        detected_language: str,
    ) -> RouteResult:
        """
        Select route based on intent, confidence, and deployment config.
        
        Implements the routing decision table from the design document:
        - general: General Q&A route
        - environment (conf >= 0.65): Environment/RAG route with retrieved docs
        - environment (conf < 0.65): Fall back to General Q&A
        - web_search (enabled): Web Search route with Tavily results
        - web_search (disabled): Fall back to General Q&A
        - small_talk: Hardcoded response, skip Call 2
        - out_of_scope: OOS response, skip Call 2
        - clarify/needs_clarification: Apply confidence-based clarification strategy
        
        Args:
            intent_result: IntentResult from the classifier containing intent,
                          confidence, needs_clarification, and query_clean
            detected_language: Detected language code ("ja", "en", or "unknown")
        
        Returns:
            RouteResult containing route_type, retrieved_context, direct_response,
            and clarification_suffix
        """
        import logging
        logger = logging.getLogger(__name__)
        
        intent = intent_result.intent
        confidence = intent_result.confidence
        needs_clarification = intent_result.needs_clarification
        
        # -----------------------------------------------------------------------
        # Routing decision table (evaluated top-to-bottom, first match wins):
        #
        #  needs_clarification OR intent == "clarify":
        #    conf < 0.30  → clarify route, direct_response = low_confidence msg
        #    conf < 0.45  → general route, clarification_suffix = partial_match msg
        #    conf >= 0.45 → fall through to normal intent routing below
        #
        #  intent == "out_of_scope"  → configured OOS direct_response, skip Call 2
        #
        #  intent == "environment":
        #    conf >= 0.65 AND docs found → environment route with RAG context
        #    conf >= 0.65 AND no docs    → fall back to general route
        #    conf < 0.65                 → fall back to general route
        #
        #  intent == "web_search":
        #    web_search_enabled AND search succeeds → web_search route with results
        #    web_search_enabled AND search fails    → fall back to general route
        #    web_search disabled                    → fall back to general route
        #
        #  default → general route (no context, no direct response)
        #  NOTE: small_talk is intentionally removed. Short replies like "yes",
        #        greetings, and casual messages are routed as "general" so they
        #        go through Call 2 and preserve conversation context.
        # -----------------------------------------------------------------------
        
        # Handle clarification strategy first (applies to any intent)
        if needs_clarification or intent == "clarify":
            # -----------------------------------------------------------------------
            # Clarification strategy thresholds:
            #   confidence < 0.30  — "low confidence": the classifier is too uncertain
            #     to attempt a response at all.  Return a direct clarification request
            #     and skip Call 2 entirely.
            #   0.30 <= confidence < 0.45 — "partial match": the classifier has a weak
            #     signal but enough to attempt a general answer.  Proceed with Call 2
            #     and append a soft clarification suffix to the response so the user
            #     knows they can rephrase if the answer missed the mark.
            #   confidence >= 0.45 — sufficient confidence; fall through to normal
            #     intent routing without any clarification handling.
            # -----------------------------------------------------------------------
            # Low confidence (< 0.3): Direct clarification response, skip Call 2
            if confidence < 0.3:
                clarification_lang = detected_language if detected_language in self.CLARIFICATION_TEMPLATES else "en"
                direct_response = self.CLARIFICATION_TEMPLATES[clarification_lang]["low_confidence"]
                logger.info(
                    f"route=clarify confidence={confidence:.2f} strategy=low_confidence direct_response=True"
                )
                return RouteResult(
                    route_type="clarify",
                    retrieved_context="",
                    direct_response=direct_response,
                    clarification_suffix=None,
                )
            
            # Partial match (0.3 <= conf < 0.45): Attempt response with clarification suffix
            elif confidence < 0.45:
                clarification_lang = detected_language if detected_language in self.CLARIFICATION_TEMPLATES else "en"
                clarification_suffix = self.CLARIFICATION_TEMPLATES[clarification_lang]["partial_match"]
                logger.info(
                    f"route=general confidence={confidence:.2f} strategy=partial_match suffix=True"
                )
                # Fall through to general route with suffix
                return RouteResult(
                    route_type="general",
                    retrieved_context="",
                    direct_response=None,
                    clarification_suffix=clarification_suffix,
                )
        
        # Route: out_of_scope
        if intent == "out_of_scope":
            oos_lang = detected_language if detected_language in self.deployment_config.out_of_scope_response else "en"
            direct_response = self.deployment_config.out_of_scope_response.get(
                oos_lang,
                self.deployment_config.out_of_scope_response.get("en", "I'm sorry, I can't help with that.")
            )
            logger.info(f"route=out_of_scope direct_response=True")
            return RouteResult(
                route_type="out_of_scope",
                retrieved_context="",
                direct_response=direct_response,
                clarification_suffix=None,
            )
        
        # Route: environment (with confidence threshold)
        if intent == "environment":
            if confidence >= 0.65:
                # Retrieve environment context
                retrieved_context = await self._retrieve_environment_context()
                
                if retrieved_context:
                    logger.info(
                        f"route=environment confidence={confidence:.2f} "
                        f"context_chars={len(retrieved_context)}"
                    )
                    return RouteResult(
                        route_type="environment",
                        retrieved_context=retrieved_context,
                        direct_response=None,
                        clarification_suffix=None,
                    )
                else:
                    # Empty RAG retrieval: fall back to general
                    logger.info(
                        f"route=environment->general confidence={confidence:.2f} "
                        f"reason=empty_rag_retrieval"
                    )
                    return RouteResult(
                        route_type="general",
                        retrieved_context="",
                        direct_response=None,
                        clarification_suffix=None,
                    )
            else:
                # Low confidence: fall back to general
                logger.info(
                    f"route=environment->general confidence={confidence:.2f} "
                    f"reason=low_confidence"
                )
                return RouteResult(
                    route_type="general",
                    retrieved_context="",
                    direct_response=None,
                    clarification_suffix=None,
                )
        
        # Route: web_search (with enabled check)
        if intent == "web_search":
            if self.deployment_config.web_search_enabled and self.tavily_client:
                # Perform web search
                try:
                    search_results = await self.tavily_client.search(intent_result.query_clean)
                    
                    if search_results:
                        logger.info(
                            f"route=web_search confidence={confidence:.2f} "
                            f"results_chars={len(search_results)}"
                        )
                        return RouteResult(
                            route_type="web_search",
                            retrieved_context=search_results,
                            direct_response=None,
                            clarification_suffix=None,
                        )
                    else:
                        # Empty search results: fall back to general
                        logger.info(
                            f"route=web_search->general confidence={confidence:.2f} "
                            f"reason=empty_search_results"
                        )
                        return RouteResult(
                            route_type="general",
                            retrieved_context="",
                            direct_response=None,
                            clarification_suffix=None,
                        )
                
                except Exception as exc:
                    # Search failure: fall back to general
                    logger.warning(
                        f"route=web_search->general confidence={confidence:.2f} "
                        f"reason=search_failure error={exc}"
                    )
                    return RouteResult(
                        route_type="general",
                        retrieved_context="",
                        direct_response=None,
                        clarification_suffix=None,
                    )
            else:
                # Web search disabled: fall back to general
                logger.info(
                    f"route=web_search->general confidence={confidence:.2f} "
                    f"reason=web_search_disabled"
                )
                return RouteResult(
                    route_type="general",
                    retrieved_context="",
                    direct_response=None,
                    clarification_suffix=None,
                )
        
        # Route: general (default)
        logger.info(f"route=general intent={intent} confidence={confidence:.2f}")
        return RouteResult(
            route_type="general",
            retrieved_context="",
            direct_response=None,
            clarification_suffix=None,
        )
    
    async def _retrieve_environment_context(self) -> str:
        """
        Retrieve environment context from deployment configuration.
        
        Phase 1 implementation: Read raw text from environment_docs files
        and concatenate them. Future: embedding-based retrieval.
        
        Returns:
            Retrieved context string (empty if no docs or retrieval fails)
        """
        import logging
        from pathlib import Path
        
        logger = logging.getLogger(__name__)
        
        if not self.deployment_config.environment_docs:
            logger.info("rag_retrieval: no environment_docs configured")
            return ""
        
        context_parts: list[str] = []
        
        for doc_entry in self.deployment_config.environment_docs:
            doc_path = doc_entry.get("path")
            if not doc_path:
                continue
            
            try:
                # Read the file
                file_path = Path(doc_path)
                if not file_path.exists():
                    logger.warning(f"rag_retrieval: file not found: {doc_path}")
                    continue
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        context_parts.append(content)
                        logger.info(f"rag_retrieval: loaded {doc_path} ({len(content)} chars)")
            
            except Exception as exc:
                logger.warning(f"rag_retrieval: failed to read {doc_path}: {exc}")
                continue
        
        # Concatenate all parts
        full_context = "\n\n".join(context_parts)
        
        # TODO: Implement token budget truncation based on model tier
        # For now, return the full concatenated context
        
        return full_context
