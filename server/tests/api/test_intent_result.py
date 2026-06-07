"""Unit tests for IntentResult field defaults and normalisation — Requirements 6.4, 6.6, 6.8, 6.9"""

from __future__ import annotations

import pytest

from server.llm.intent import IntentResult, _VALID_INTENTS, IntentClassifier


def _make(intent="general", **kwargs) -> IntentResult:
    defaults = dict(
        language="en",
        confidence=0.9,
        needs_clarification=False,
        clarification_reason=None,
        query_clean="test",
    )
    defaults.update(kwargs)
    return IntentResult(intent=intent, **defaults)


class TestIntentResultFields:

    def test_destination_query_empty_string_normalised_to_none(self) -> None:
        result = _make(destination_query="")
        assert result.destination_query is None

    def test_destination_query_nonempty_preserved(self) -> None:
        result = _make(destination_query="cafeteria")
        assert result.destination_query == "cafeteria"

    def test_destination_query_defaults_to_none(self) -> None:
        result = _make()
        assert result.destination_query is None

    def test_accessibility_flag_defaults_to_false(self) -> None:
        result = _make()
        assert result.accessibility_flag is False
        assert isinstance(result.accessibility_flag, bool)

    def test_accessibility_flag_true_stored_correctly(self) -> None:
        result = _make(accessibility_flag=True)
        assert result.accessibility_flag is True
        assert isinstance(result.accessibility_flag, bool)

    def test_legacy_intents_accepted(self) -> None:
        for intent in ["general", "environment", "web_search", "out_of_scope", "clarify"]:
            result = _make(intent=intent)
            assert result.intent == intent
            assert result.destination_query is None
            assert result.accessibility_flag is False

    def test_new_intents_accepted(self) -> None:
        result = _make(intent="navigation", destination_query="elevator")
        assert result.intent == "navigation"
        assert result.destination_query == "elevator"

        result2 = _make(intent="accessibility_request", accessibility_flag=True)
        assert result2.intent == "accessibility_request"
        assert result2.accessibility_flag is True

    def test_invalid_intent_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid intent"):
            _make(intent="unknown_intent_xyz")

    def test_confidence_clamped_above_one(self) -> None:
        result = _make(confidence=1.5)
        assert result.confidence == 1.0

    def test_confidence_clamped_below_zero(self) -> None:
        result = _make(confidence=-0.3)
        assert result.confidence == 0.0

    def test_valid_intents_frozenset_contains_new_values(self) -> None:
        assert "navigation" in _VALID_INTENTS
        assert "accessibility_request" in _VALID_INTENTS

    def test_valid_intents_frozenset_still_has_legacy_values(self) -> None:
        for intent in ["general", "environment", "web_search", "out_of_scope", "clarify"]:
            assert intent in _VALID_INTENTS


class TestIntentClassifierMissingFields:
    """Verify that the classifier handles LLM JSON missing the new fields gracefully."""

    def _parse_data(self, data: dict) -> IntentResult:
        """Simulate what IntentClassifier.classify() does when parsing LLM JSON."""
        return IntentResult(
            intent=data["intent"],
            language=data["language"],
            confidence=float(data["confidence"]),
            needs_clarification=bool(data["needs_clarification"]),
            clarification_reason=data.get("clarification_reason"),
            query_clean=data["query_clean"],
            destination_query=data.get("destination_query") or None,
            accessibility_flag=bool(data.get("accessibility_flag", False)),
        )

    def test_missing_destination_query_defaults_to_none(self) -> None:
        data = {
            "intent": "general", "language": "en", "confidence": 0.9,
            "needs_clarification": False, "clarification_reason": None,
            "query_clean": "test",
            # destination_query absent
        }
        result = self._parse_data(data)
        assert result.destination_query is None

    def test_missing_accessibility_flag_defaults_to_false(self) -> None:
        data = {
            "intent": "general", "language": "en", "confidence": 0.9,
            "needs_clarification": False, "clarification_reason": None,
            "query_clean": "test",
            # accessibility_flag absent
        }
        result = self._parse_data(data)
        assert result.accessibility_flag is False

    def test_both_fields_missing_no_exception(self) -> None:
        data = {
            "intent": "navigation", "language": "en", "confidence": 0.95,
            "needs_clarification": False, "clarification_reason": None,
            "query_clean": "find cafeteria",
            # both new fields absent
        }
        result = self._parse_data(data)
        assert result.destination_query is None
        assert result.accessibility_flag is False
