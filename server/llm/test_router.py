"""
Unit tests for the Router module.

Tests the routing decision table, confidence thresholds, context retrieval,
and direct response handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from server.llm.router import Router, RouteResult
from server.llm.assembler import DeploymentConfig
from server.llm.intent import IntentResult


@pytest.fixture
def default_config():
    """Create a default deployment config for testing."""
    return DeploymentConfig.default()


@pytest.fixture
def router_no_search(default_config):
    """Create a Router instance without search client."""
    return Router(
        deployment_config=default_config,
        tavily_client=None,
    )


@pytest.fixture
def router_with_search(default_config):
    """Create a Router instance with mocked search client."""
    mock_search = AsyncMock()
    return Router(
        deployment_config=default_config,
        tavily_client=mock_search,
    )


@pytest.mark.asyncio
async def test_route_general_intent(router_no_search):
    """Test routing for general intent."""
    intent_result = IntentResult(
        intent="general",
        language="en",
        confidence=0.8,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="What is the weather today?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "general"
    assert result.retrieved_context == ""
    assert result.direct_response is None
    assert result.clarification_suffix is None


@pytest.mark.asyncio
async def test_route_small_talk_greeting(router_no_search):
    """Test routing for small talk with greeting."""
    intent_result = IntentResult(
        intent="small_talk",
        language="en",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Hello there!",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "small_talk"
    assert result.retrieved_context == ""
    assert result.direct_response == "Hello! How can I help you?"
    assert result.clarification_suffix is None


@pytest.mark.asyncio
async def test_route_small_talk_japanese(router_no_search):
    """Test routing for small talk in Japanese."""
    intent_result = IntentResult(
        intent="small_talk",
        language="ja",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="こんにちは",
    )
    
    result = await router_no_search.route(intent_result, "ja")
    
    assert result.route_type == "small_talk"
    assert result.direct_response == "こんにちは！何かお手伝いできることはありますか？"


@pytest.mark.asyncio
async def test_route_out_of_scope(router_no_search):
    """Test routing for out of scope intent."""
    intent_result = IntentResult(
        intent="out_of_scope",
        language="en",
        confidence=0.7,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Can you hack into a system?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "out_of_scope"
    assert result.retrieved_context == ""
    assert result.direct_response == "I'm sorry, I can't help with that."
    assert result.clarification_suffix is None


@pytest.mark.asyncio
async def test_route_clarify_low_confidence(router_no_search):
    """Test routing for clarify with low confidence (< 0.3)."""
    intent_result = IntentResult(
        intent="clarify",
        language="en",
        confidence=0.2,
        needs_clarification=True,
        clarification_reason="Unclear intent",
        query_clean="um... uh...",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "clarify"
    assert result.retrieved_context == ""
    assert result.direct_response == "I'm sorry, I didn't quite understand that. Could you please rephrase?"
    assert result.clarification_suffix is None


@pytest.mark.asyncio
async def test_route_clarify_partial_match(router_no_search):
    """Test routing for clarify with partial confidence (0.3 <= conf < 0.45)."""
    intent_result = IntentResult(
        intent="general",
        language="en",
        confidence=0.35,
        needs_clarification=True,
        clarification_reason="Ambiguous query",
        query_clean="Tell me about it",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "general"
    assert result.retrieved_context == ""
    assert result.direct_response is None
    assert result.clarification_suffix == "If I misunderstood, please let me know."


@pytest.mark.asyncio
async def test_route_clarify_high_confidence(router_no_search):
    """Test routing for clarify with high confidence (>= 0.45) proceeds normally."""
    intent_result = IntentResult(
        intent="general",
        language="en",
        confidence=0.5,
        needs_clarification=True,
        clarification_reason="Minor ambiguity",
        query_clean="What's the time?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "general"
    assert result.direct_response is None
    assert result.clarification_suffix is None


@pytest.mark.asyncio
async def test_route_environment_high_confidence_no_docs(router_no_search):
    """Test environment route with high confidence but no docs configured."""
    intent_result = IntentResult(
        intent="environment",
        language="en",
        confidence=0.7,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Where is the meeting room?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    # Should fall back to general when no context is retrieved
    assert result.route_type == "general"
    assert result.retrieved_context == ""


@pytest.mark.asyncio
async def test_route_environment_low_confidence(router_no_search):
    """Test environment route with low confidence (< 0.65) falls back to general."""
    intent_result = IntentResult(
        intent="environment",
        language="en",
        confidence=0.5,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Maybe something about the building?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "general"
    assert result.retrieved_context == ""


@pytest.mark.asyncio
async def test_route_web_search_disabled(router_no_search):
    """Test web search route when search is disabled falls back to general."""
    intent_result = IntentResult(
        intent="web_search",
        language="en",
        confidence=0.8,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="What's the latest news?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.route_type == "general"
    assert result.retrieved_context == ""


@pytest.mark.asyncio
async def test_route_web_search_enabled_success(router_with_search):
    """Test web search route when enabled and search succeeds."""
    # Enable web search in config
    router_with_search.deployment_config.web_search_enabled = True
    
    # Mock search results
    router_with_search.tavily_client.search.return_value = "Search result: Latest news about AI"
    
    intent_result = IntentResult(
        intent="web_search",
        language="en",
        confidence=0.8,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="What's the latest news about AI?",
    )
    
    result = await router_with_search.route(intent_result, "en")
    
    assert result.route_type == "web_search"
    assert result.retrieved_context == "Search result: Latest news about AI"
    assert result.direct_response is None


@pytest.mark.asyncio
async def test_route_web_search_enabled_empty_results(router_with_search):
    """Test web search route when search returns empty results."""
    # Enable web search in config
    router_with_search.deployment_config.web_search_enabled = True
    
    # Mock empty search results
    router_with_search.tavily_client.search.return_value = ""
    
    intent_result = IntentResult(
        intent="web_search",
        language="en",
        confidence=0.8,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="What's the latest news?",
    )
    
    result = await router_with_search.route(intent_result, "en")
    
    # Should fall back to general when search returns empty
    assert result.route_type == "general"
    assert result.retrieved_context == ""


@pytest.mark.asyncio
async def test_route_web_search_enabled_failure(router_with_search):
    """Test web search route when search fails with exception."""
    # Enable web search in config
    router_with_search.deployment_config.web_search_enabled = True
    
    # Mock search failure
    router_with_search.tavily_client.search.side_effect = Exception("Search API error")
    
    intent_result = IntentResult(
        intent="web_search",
        language="en",
        confidence=0.8,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="What's the latest news?",
    )
    
    result = await router_with_search.route(intent_result, "en")
    
    # Should fall back to general when search fails
    assert result.route_type == "general"
    assert result.retrieved_context == ""


@pytest.mark.asyncio
async def test_small_talk_response_thanks(router_no_search):
    """Test small talk response selection for thanks."""
    intent_result = IntentResult(
        intent="small_talk",
        language="en",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Thank you so much!",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.direct_response == "You're welcome!"


@pytest.mark.asyncio
async def test_small_talk_response_goodbye(router_no_search):
    """Test small talk response selection for goodbye."""
    intent_result = IntentResult(
        intent="small_talk",
        language="en",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Goodbye!",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.direct_response == "Goodbye! Feel free to reach out if you need anything."


@pytest.mark.asyncio
async def test_small_talk_response_how_are_you(router_no_search):
    """Test small talk response selection for how are you."""
    intent_result = IntentResult(
        intent="small_talk",
        language="en",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="How are you doing?",
    )
    
    result = await router_no_search.route(intent_result, "en")
    
    assert result.direct_response == "I'm doing well, thank you!"


@pytest.mark.asyncio
async def test_clarification_japanese(router_no_search):
    """Test clarification templates in Japanese."""
    intent_result = IntentResult(
        intent="clarify",
        language="ja",
        confidence=0.25,
        needs_clarification=True,
        clarification_reason="不明確",
        query_clean="えーと...",
    )
    
    result = await router_no_search.route(intent_result, "ja")
    
    assert result.route_type == "clarify"
    assert result.direct_response == "申し訳ありませんが、よく理解できませんでした。もう一度お願いできますか？"


@pytest.mark.asyncio
async def test_unknown_language_defaults_to_english(router_no_search):
    """Test that unknown language defaults to English for templates."""
    intent_result = IntentResult(
        intent="small_talk",
        language="unknown",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="Hello",
    )
    
    result = await router_no_search.route(intent_result, "unknown")
    
    # Should use English templates as fallback
    assert result.direct_response == "Hello! How can I help you?"
