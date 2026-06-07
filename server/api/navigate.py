"""
POST /api/navigate — Navigation intent orchestration endpoint.

Runs the two-call LLM pipeline (intent classify → response generate) with
building context injected, maintains per-session conversation history, and
returns a structured NavigateResponse.

The core logic lives in ``run_navigate_turn()`` — a standalone async function
that is free of asyncio queues and pipeline state so it can be unit-tested and
called from both this REST endpoint and (if ever needed) other entry points.

Privacy: utterance text and response text are never logged at INFO level.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Custom exceptions used to map pipeline failures to HTTP 502 responses
# ---------------------------------------------------------------------------

class ClassifyError(Exception):
    """Raised when the intent classification call (Call 1) fails."""


class LLMError(Exception):
    """Raised when the LLM response generation call (Call 2) fails."""


# ---------------------------------------------------------------------------
# Session management dataclass
# ---------------------------------------------------------------------------

@dataclass
class NavigateSession:
    """In-memory conversation session for the /api/navigate REST path.

    Attributes:
        history:     OpenAI-format message list (role/content dicts only).
        last_active: UTC timestamp of the most recent request — used for TTL expiry.
    """
    history: list[dict] = field(default_factory=list)
    last_active: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Internal result dataclass (not exposed via HTTP directly)
# ---------------------------------------------------------------------------

@dataclass
class NavigateTurnResult:
    """Result of one LLM turn, returned by ``run_navigate_turn()``."""
    intent: str
    destination_query: str | None
    accessibility_flag: bool
    response_text: str
    needs_clarification: bool
    language: str


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class BuildingContext(BaseModel):
    current_node_label: str
    available_pois: list[str]
    floor_name: str


class NavigateRequest(BaseModel):
    text: str
    language: str
    session_id: str
    building_context: BuildingContext

    @field_validator("text")
    @classmethod
    def text_not_empty_or_whitespace(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must be a non-empty, non-whitespace string")
        return v


class NavigateResponse(BaseModel):
    intent: str
    destination_query: str | None
    accessibility_flag: bool
    response_text: str
    needs_clarification: bool
    language: str
    session_id: str


# ---------------------------------------------------------------------------
# Core pipeline function — free of asyncio queues and PipelineState
# ---------------------------------------------------------------------------

async def run_navigate_turn(
    text: str,
    language: str,
    session_history: list[dict],
    building_context: dict | None,
    intent_classifier: Any,
    prompt_assembler: Any,
    llm_chain: Any,
    router_component: Any,
    post_processor: Any,
) -> NavigateTurnResult:
    """Execute one LLM turn for the navigate endpoint.

    This function mirrors the per-turn logic in ``VoicePipeline._llm_worker``
    but has **no** dependency on ``asyncio.Queue``, ``InterruptController``, or
    ``PipelineState``.  It batches the LLM stream into a single string instead
    of pushing tokens to a queue.

    Args:
        text:              The user utterance.
        language:          Detected language code (en/ja/zh/ko).
        session_history:   OpenAI-format message list for this session.
        building_context:  Dict with ``current_node_label``, ``available_pois``,
                           ``floor_name``; injected into the system prompt.
        intent_classifier: ``app.state.intent_classifier`` instance.
        prompt_assembler:  ``app.state.prompt_assembler`` instance.
        llm_chain:         ``app.state.llm_chain`` instance.
        router_component:  ``app.state.router`` instance.
        post_processor:    ``app.state.post_processor`` instance.

    Returns:
        ``NavigateTurnResult`` with all response fields populated.

    Raises:
        ClassifyError: If the intent classification call raises.
        LLMError:      If the LLM generation call raises or yields no tokens.
    """
    # -- Call 1: Intent classification ----------------------------------------
    try:
        intent_result = await intent_classifier.classify(text)
    except Exception as exc:
        raise ClassifyError(str(exc)) from exc

    # -- Route decision --------------------------------------------------------
    try:
        route_result = await router_component.route(intent_result, language)
    except Exception as exc:
        # Routing is non-fatal — fall back to a general route
        logger.warning("navigate_turn: router.route raised %s — falling back to general", exc)
        from server.llm.router import RouteResult
        route_result = RouteResult(
            route_type="general",
            retrieved_context="",
            direct_response=None,
            clarification_suffix=None,
        )

    # -- Direct response (OOS / low-confidence clarify) — skip Call 2 ---------
    if route_result.direct_response is not None:
        response_text = route_result.direct_response
        return NavigateTurnResult(
            intent=intent_result.intent,
            destination_query=intent_result.destination_query,
            accessibility_flag=intent_result.accessibility_flag,
            response_text=response_text,
            needs_clarification=intent_result.needs_clarification,
            language=intent_result.language if intent_result.language != "unknown" else language,
        )

    # -- Call 2: LLM response generation --------------------------------------
    _, messages = prompt_assembler.assemble_prompt(
        user_input=text,
        intent_result=intent_result,
        session_history=session_history,
        retrieved_context=route_result.retrieved_context,
        route_type=route_result.route_type,
        building_context=building_context,
    )

    try:
        tokens: list[str] = []
        async for token in llm_chain.stream(messages, max_tokens=200, temperature=0.65):
            tokens.append(token)
    except Exception as exc:
        raise LLMError(str(exc)) from exc

    if not tokens:
        raise LLMError("LLM stream produced no tokens")

    raw_response = "".join(tokens)

    # Resolve effective language for post-processing
    effective_lang = intent_result.language if intent_result.language != "unknown" else language

    # Clean markdown / whitespace for TTS consumption
    response_text = post_processor.clean(raw_response, effective_lang)

    # Append clarification suffix when the router requested one
    if route_result.clarification_suffix:
        response_text = f"{response_text} {route_result.clarification_suffix}".strip()

    return NavigateTurnResult(
        intent=intent_result.intent,
        destination_query=intent_result.destination_query,
        accessibility_flag=intent_result.accessibility_flag,
        response_text=response_text,
        needs_clarification=intent_result.needs_clarification,
        language=effective_lang,
    )


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

@router.post("/navigate", response_model=NavigateResponse)
async def navigate_endpoint(
    request: Request,
    body: NavigateRequest,
) -> NavigateResponse:
    """Run the navigation intent pipeline and return a structured response.

    Session history is maintained in ``app.state.navigate_sessions`` keyed
    by ``session_id``.  Sessions expire after 30 minutes of inactivity via the
    background cleanup task started in ``server/main.py``.

    Returns HTTP 422 for validation errors, HTTP 502 for backend failures.
    Privacy: text content is never logged at INFO level.
    """
    t0 = time.monotonic()

    # -- Session lookup / creation --------------------------------------------
    sessions: dict[str, NavigateSession] = request.app.state.navigate_sessions
    session = sessions.get(body.session_id)
    if session is None:
        session = NavigateSession()
        sessions[body.session_id] = session
    session.last_active = datetime.utcnow()

    # -- Read session_memory_turns from deployment config ---------------------
    session_memory_turns: int = getattr(
        request.app.state.deployment_config, "session_memory_turns", 6
    )

    # -- Run LLM turn ---------------------------------------------------------
    try:
        result = await run_navigate_turn(
            text=body.text,
            language=body.language,
            session_history=list(session.history),
            building_context=body.building_context.model_dump(),
            intent_classifier=request.app.state.intent_classifier,
            prompt_assembler=request.app.state.prompt_assembler,
            llm_chain=request.app.state.llm_chain,
            router_component=request.app.state.router,
            post_processor=request.app.state.post_processor,
        )
    except ClassifyError as exc:
        logger.debug("navigate_classify_failed  detail=%s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "classify_failed", "detail": str(exc)},
        )
    except LLMError as exc:
        logger.debug("navigate_llm_failed  detail=%s", exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "llm_failed", "detail": str(exc)},
        )

    # -- Update session history -----------------------------------------------
    session.history.append({"role": "user", "content": body.text})
    session.history.append({"role": "assistant", "content": result.response_text})

    # Trim to session_memory_turns pairs (oldest pair removed first)
    max_entries = session_memory_turns * 2
    while len(session.history) > max_entries:
        session.history.pop(0)
        session.history.pop(0)

    latency_ms = int((time.monotonic() - t0) * 1000)
    # Log metadata only — never log utterance text or response text (privacy/FR-017)
    logger.info(
        "navigate  status=200  latency_ms=%d  language=%s  intent=%s  session_id=%.8s",
        latency_ms,
        result.language,
        result.intent,
        body.session_id,
    )

    return NavigateResponse(
        intent=result.intent,
        destination_query=result.destination_query,
        accessibility_flag=result.accessibility_flag,
        response_text=result.response_text,
        needs_clarification=result.needs_clarification,
        language=result.language,
        session_id=body.session_id,
    )
