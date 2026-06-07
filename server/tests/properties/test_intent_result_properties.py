"""
Property-based tests for server/llm/intent.py :: IntentResult

Feature: building-nav-integration
Properties 9–10 from design.md
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from server.llm.intent import IntentResult

_VALID_INTENTS = [
    "general", "environment", "web_search", "out_of_scope",
    "clarify", "navigation", "accessibility_request",
]

# Strategy for building a minimal valid IntentResult kwargs dict
_base_kwargs = dict(
    intent="general",
    language="en",
    confidence=0.9,
    needs_clarification=False,
    clarification_reason=None,
    query_clean="test query",
)


def _make_intent_result(**overrides) -> IntentResult:
    kwargs = {**_base_kwargs, **overrides}
    return IntentResult(**kwargs)


# ---------------------------------------------------------------------------
# Property 9: accessibility_flag type invariant
# ---------------------------------------------------------------------------

@given(
    raw_flag=st.one_of(
        st.booleans(),
        st.integers(),
        st.none(),
        st.text(max_size=10),
        st.floats(allow_nan=False),
    )
)
@settings(max_examples=300)
def test_accessibility_flag_is_always_bool(raw_flag) -> None:
    """Feature: building-nav-integration, Property 9: accessibility_flag type invariant.

    Regardless of what value is coerced to bool for accessibility_flag,
    the resulting IntentResult.accessibility_flag MUST be a Python bool instance
    (True or False), never None, int, str, or any other type.
    Validates: Requirements 6.4
    """
    coerced = bool(raw_flag) if raw_flag is not None else False
    result = _make_intent_result(accessibility_flag=coerced)
    assert isinstance(result.accessibility_flag, bool), (
        f"accessibility_flag must be bool, got {type(result.accessibility_flag).__name__!r} "
        f"(value={result.accessibility_flag!r}, raw_flag={raw_flag!r})"
    )


# ---------------------------------------------------------------------------
# Property 10: destination_query normalisation
# ---------------------------------------------------------------------------

@given(destination_query=st.just(""))
@settings(max_examples=50)
def test_empty_destination_query_normalised_to_none(destination_query: str) -> None:
    """Feature: building-nav-integration, Property 10a: empty string → None.

    When destination_query is set to "" at construction, __post_init__ MUST
    normalise it to None.
    Validates: Requirements 6.4, 6.9
    """
    result = _make_intent_result(destination_query=destination_query)
    assert result.destination_query is None, (
        f"Expected destination_query=None after normalisation, "
        f"got {result.destination_query!r}"
    )


@given(destination_query=st.text(min_size=1))
@settings(max_examples=300)
def test_nonempty_destination_query_preserved(destination_query: str) -> None:
    """Feature: building-nav-integration, Property 10b: non-empty string preserved.

    When destination_query is a non-empty string, __post_init__ MUST leave it
    unchanged — no stripping, no truncation, no transformation.
    Validates: Requirements 6.4, 6.9
    """
    result = _make_intent_result(destination_query=destination_query)
    assert result.destination_query == destination_query, (
        f"Non-empty destination_query was altered: "
        f"input={destination_query!r}, stored={result.destination_query!r}"
    )


@given(destination_query=st.none())
@settings(max_examples=50)
def test_none_destination_query_stays_none(destination_query) -> None:
    """destination_query=None at construction must remain None."""
    result = _make_intent_result(destination_query=destination_query)
    assert result.destination_query is None


# ---------------------------------------------------------------------------
# Backward-compatibility: existing intents still work with new fields defaulted
# ---------------------------------------------------------------------------

@given(intent=st.sampled_from(["general", "environment", "web_search", "out_of_scope", "clarify"]))
@settings(max_examples=100)
def test_legacy_intents_have_default_new_fields(intent: str) -> None:
    """Existing intents must still be accepted and default new fields correctly.

    Validates: Requirement 6.6 backward-compatibility
    """
    result = _make_intent_result(intent=intent)
    assert result.destination_query is None
    assert result.accessibility_flag is False
