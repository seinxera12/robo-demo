"""
Intent classifier module for the Robo LLM pipeline.

Provides LLM-based intent classification that determines intent category,
detected language, and confidence score through a dedicated first-pass LLM
call (Call 1) using a structured JSON classifier prompt.

The classifier replaces the previous keyword-regex approach with a more
flexible LLM-driven strategy that supports six intent categories:
"general", "environment", "web_search", "small_talk", "out_of_scope",
and "clarify".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from server.llm.chain import LLMChain

logger = logging.getLogger(__name__)

# Valid intent values for IntentResult
_VALID_INTENTS = frozenset([
    "general",
    "environment",
    "web_search",
    "small_talk",
    "out_of_scope",
    "clarify",
])


@dataclass
class IntentResult:
    """Structured output from the LLM-based intent classifier.
    
    Attributes:
        intent: One of "general", "environment", "web_search", "small_talk", 
                "out_of_scope", or "clarify"
        language: Detected language code ("ja", "en", or "unknown")
        confidence: Confidence score in range [0.0, 1.0]
        needs_clarification: Whether the user input requires clarification
        clarification_reason: Optional explanation of why clarification is needed
        query_clean: User intent restated clearly for downstream processing
    """
    intent: str
    language: str
    confidence: float
    needs_clarification: bool
    clarification_reason: str | None
    query_clean: str
    
    def __post_init__(self) -> None:
        """Validate and normalize field values after initialization."""
        # Validate intent is one of the six valid values
        if self.intent not in _VALID_INTENTS:
            raise ValueError(
                f"Invalid intent '{self.intent}'. Must be one of: "
                f"{', '.join(sorted(_VALID_INTENTS))}"
            )
        
        # Clamp confidence to [0.0, 1.0]
        self.confidence = max(0.0, min(1.0, self.confidence))


class IntentClassifier:
    """LLM-based intent classifier.

    Makes a dedicated LLM call with a classifier prompt to determine intent,
    language, and confidence. Uses different prompts for groq vs small model tiers.
    """

    def __init__(self, llm_chain: LLMChain, model_tier: str) -> None:
        """Initialize the intent classifier.
        
        Args:
            llm_chain: The LLM chain to use for classification calls
            model_tier: Either "groq" or "small" to select appropriate prompts
        """
        self._llm = llm_chain
        self._model_tier = model_tier
        
        # Load classifier prompt based on model tier
        prompts_dir = Path(__file__).parent.parent / "prompts"
        if model_tier == "small":
            prompt_file = prompts_dir / "classifier_small.txt"
        else:
            prompt_file = prompts_dir / "classifier.txt"
        
        with open(prompt_file, "r", encoding="utf-8") as f:
            self._classifier_prompt = f.read()
        
        logger.info(f"IntentClassifier initialized with model_tier={model_tier}")

    async def classify(self, transcript: str) -> IntentResult:
        """Classify transcript using LLM call.

        Args:
            transcript: The transcribed user utterance.

        Returns:
            IntentResult with intent, language, confidence, and other fields.
            Falls back to default IntentResult on JSON parse failure.
        """
        # Prepare user message
        user_message = transcript
        if self._model_tier == "small":
            user_message = f"Classify this input and return only JSON:\n\n{transcript}"
        
        # Build messages for classifier call
        messages = [
            {"role": "system", "content": self._classifier_prompt},
            {"role": "user", "content": user_message},
        ]
        
        # Make LLM call with classifier parameters
        raw_output_parts: list[str] = []
        try:
            async for token in self._llm.stream(
                messages,
                max_tokens=150,
                temperature=0.0,
            ):
                raw_output_parts.append(token)
        except Exception as exc:
            logger.warning(f"Classifier LLM call failed: {exc}")
            return self._default_result(transcript)
        
        raw_output = "".join(raw_output_parts).strip()
        
        # Parse JSON response
        try:
            # Extract JSON from markdown code blocks if present
            if "```json" in raw_output:
                start = raw_output.find("```json") + 7
                end = raw_output.find("```", start)
                if end != -1:
                    raw_output = raw_output[start:end].strip()
            elif "```" in raw_output:
                start = raw_output.find("```") + 3
                end = raw_output.find("```", start)
                if end != -1:
                    raw_output = raw_output[start:end].strip()
            
            data = json.loads(raw_output)
            
            # Validate required fields
            required_fields = ["intent", "language", "confidence", 
                             "needs_clarification", "query_clean"]
            for field in required_fields:
                if field not in data:
                    raise ValueError(f"Missing required field: {field}")
            
            # Build IntentResult
            return IntentResult(
                intent=data["intent"],
                language=data["language"],
                confidence=float(data["confidence"]),
                needs_clarification=bool(data["needs_clarification"]),
                clarification_reason=data.get("clarification_reason"),
                query_clean=data["query_clean"],
            )
        
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning(
                f"Classifier JSON parse failed: {exc}. Raw output: {raw_output[:200]}"
            )
            return self._default_result(raw_output)
    
    def _default_result(self, raw_output: str) -> IntentResult:
        """Return default IntentResult on parse failure.
        
        Args:
            raw_output: The raw output from the LLM (or error message)
        
        Returns:
            Default IntentResult with general intent and 0.5 confidence
        """
        return IntentResult(
            intent="general",
            language="unknown",
            confidence=0.5,
            needs_clarification=False,
            clarification_reason=None,
            query_clean=raw_output[:200],
        )
