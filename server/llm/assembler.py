"""
Prompt assembly module for the Robo LLM pipeline.

This module provides the DeploymentConfig dataclass and PromptAssembler class
for building modular, context-aware system prompts from independent blocks.
"""

from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class DeploymentConfig:
    """
    Deployment-specific configuration loaded from config/deployment.yaml.
    
    This dataclass holds all deployment environment settings including
    identity, language preferences, capabilities, and out-of-scope responses.
    """
    
    deployment_id: str = "default"
    deployment_type: str = "desktop"  # "reception" | "enterprise" | "desktop"
    location_name: str = "Robo Assistant"
    language_primary: str = "en"  # "ja" | "en"
    language_secondary: str = "ja"  # "ja" | "en"
    tone_override: str | None = None  # None | "formal" | "casual"
    environment_docs: list[dict] = field(default_factory=list)
    session_memory_turns: int = 6
    web_search_enabled: bool = False
    out_of_scope_response: dict = field(default_factory=lambda: {
        "ja": "申し訳ありませんが、それについてはお答えできません。",
        "en": "I'm sorry, I can't help with that.",
    })
    
    @classmethod
    def from_yaml(cls, path: str) -> "DeploymentConfig":
        """
        Load deployment configuration from a YAML file.
        
        Args:
            path: Path to the deployment.yaml file
            
        Returns:
            DeploymentConfig instance populated from the YAML file
            
        Raises:
            FileNotFoundError: If the YAML file does not exist
            yaml.YAMLError: If the YAML file is malformed
        """
        yaml_path = Path(path)
        
        if not yaml_path.exists():
            raise FileNotFoundError(f"Deployment config file not found: {path}")
        
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        # Handle None case (empty YAML file)
        if data is None:
            data = {}
        
        # Create instance with loaded data, using defaults for missing fields
        return cls(
            deployment_id=data.get('deployment_id', cls.__dataclass_fields__['deployment_id'].default),
            deployment_type=data.get('deployment_type', cls.__dataclass_fields__['deployment_type'].default),
            location_name=data.get('location_name', cls.__dataclass_fields__['location_name'].default),
            language_primary=data.get('language_primary', cls.__dataclass_fields__['language_primary'].default),
            language_secondary=data.get('language_secondary', cls.__dataclass_fields__['language_secondary'].default),
            tone_override=data.get('tone_override'),
            environment_docs=data.get('environment_docs', []),
            session_memory_turns=data.get('session_memory_turns', cls.__dataclass_fields__['session_memory_turns'].default),
            web_search_enabled=data.get('web_search_enabled', cls.__dataclass_fields__['web_search_enabled'].default),
            out_of_scope_response=data.get('out_of_scope_response', cls.__dataclass_fields__['out_of_scope_response'].default_factory()),
        )
    
    @classmethod
    def default(cls) -> "DeploymentConfig":
        """
        Create a DeploymentConfig instance with built-in default values.
        
        This is used when config/deployment.yaml is absent or cannot be loaded.
        
        Returns:
            DeploymentConfig instance with default values
        """
        return cls()


def _render_building_context_block(ctx: dict) -> str:
    """Render a building context block from a context dict.

    Args:
        ctx: Dict with keys ``current_node_label``, ``available_pois`` (list),
             and ``floor_name``.

    Returns:
        Formatted building context string to inject into the system prompt.
    """
    pois = ", ".join(ctx.get("available_pois", []))
    return (
        "# Building Context\n"
        f"Current location: {ctx.get('current_node_label', 'unknown')}\n"
        f"Floor: {ctx.get('floor_name', 'unknown')}\n"
        f"Available destinations in this building: {pois}\n"
        "Only suggest destinations from this list. Do not invent locations."
    )


def detect_model_tier(model_name: str) -> str:
    """
    Detect the model tier based on the model name.
    
    Args:
        model_name: The name of the LLM model (e.g., "llama-3.3-70b-versatile", "qwen2.5:7b")
        
    Returns:
        "groq" if the model name contains "llama" or "gemini" (case-insensitive),
        "small" otherwise
        
    Examples:
        >>> detect_model_tier("llama-3.3-70b-versatile")
        'groq'
        >>> detect_model_tier("LLAMA-3.1")
        'groq'
        >>> detect_model_tier("gemini-pro")
        'groq'
        >>> detect_model_tier("qwen2.5:7b")
        'small'
        >>> detect_model_tier("gpt-4")
        'small'
    """
    model_name_lower = model_name.lower()
    
    if "llama" in model_name_lower or "gemini" in model_name_lower:
        return "groq"
    
    return "small"


class PromptAssembler:
    """
    Assembles system prompts from modular blocks loaded from files.
    
    This class loads and caches prompt template files at construction time,
    then assembles them into complete system prompts based on the deployment
    configuration, detected language, and route type.
    """
    
    def __init__(
        self,
        prompts_dir: str,
        deployment_config: DeploymentConfig,
        model_tier: str,
    ) -> None:
        """
        Initialize the PromptAssembler and load all prompt files.
        
        Args:
            prompts_dir: Path to the directory containing prompt template files
            deployment_config: Deployment configuration for this environment
            model_tier: Model tier ("groq" or "small") for selecting appropriate prompts
            
        Raises:
            FileNotFoundError: If any required prompt file is missing
        """
        self.prompts_dir = Path(prompts_dir)
        self.deployment_config = deployment_config
        self.model_tier = model_tier
        self._prompt_cache: dict[str, str] = {}
        
        # Load all required prompt files
        self._load_prompt_files()
    
    def _load_prompt_files(self) -> None:
        """
        Load and cache all prompt template files.
        
        Required files:
        - base.txt or base_small.txt (depending on model tier)
        - lang_ja.txt
        - lang_en.txt
        - lang_unknown.txt
        
        Optional files:
        - lang_ja_formal.txt (falls back to lang_ja.txt with warning)
        
        Raises:
            FileNotFoundError: If any required file is missing
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Determine which base prompt to use based on model tier
        base_file = "base_small.txt" if self.model_tier == "small" else "base.txt"
        
        # Required files
        required_files = [
            base_file,
            "lang_ja.txt",
            "lang_en.txt",
            "lang_unknown.txt",
        ]
        
        # Load required files
        for filename in required_files:
            file_path = self.prompts_dir / filename
            if not file_path.exists():
                raise FileNotFoundError(
                    f"Required prompt file not found: {file_path}"
                )
            
            with open(file_path, 'r', encoding='utf-8') as f:
                self._prompt_cache[filename] = f.read()
        
        # Handle optional lang_ja_formal.txt
        formal_path = self.prompts_dir / "lang_ja_formal.txt"
        if formal_path.exists():
            with open(formal_path, 'r', encoding='utf-8') as f:
                self._prompt_cache["lang_ja_formal.txt"] = f.read()
        else:
            # Fall back to lang_ja.txt
            logger.warning(
                "Optional prompt file lang_ja_formal.txt not found, "
                "falling back to lang_ja.txt for formal tone"
            )
            self._prompt_cache["lang_ja_formal.txt"] = self._prompt_cache["lang_ja.txt"]

        # Load optional lang_ko.txt and lang_zh.txt — fall back to lang_unknown.txt with warning
        for filename in ["lang_ko.txt", "lang_zh.txt"]:
            lang_path = self.prompts_dir / filename
            if lang_path.exists():
                with open(lang_path, 'r', encoding='utf-8') as f:
                    self._prompt_cache[filename] = f.read()
            else:
                logger.warning(
                    "Prompt file %s not found, falling back to lang_unknown.txt", filename
                )
                self._prompt_cache[filename] = self._prompt_cache["lang_unknown.txt"]
    
    def _render_deployment_block(self, detected_language: str) -> str:
        """
        Render the deployment block from DeploymentConfig fields.
        
        The deployment block includes:
        - Location name
        - Deployment type description (role description)
        - Out-of-scope response for the detected language
        
        Args:
            detected_language: The detected language code ("ja", "en", or "unknown")
            
        Returns:
            Rendered deployment block as a string
        """
        # Deployment type descriptions
        DEPLOYMENT_DESCRIPTIONS = {
            "reception": "You help visitors and staff at the building entrance. Answer questions about floors, rooms, tenants, facilities, and directions. You do not handle bookings or security matters.",
            "enterprise": "You assist employees with internal questions about the company, facilities, and procedures. You have access to internal documentation.",
            "desktop": "You are a personal assistant on the user's computer. Help with general questions, productivity tasks, and information lookup."
        }
        
        # Get deployment type description
        deployment_type_desc = DEPLOYMENT_DESCRIPTIONS.get(
            self.deployment_config.deployment_type,
            DEPLOYMENT_DESCRIPTIONS["desktop"]  # Default to desktop if unknown type
        )
        
        # Select out-of-scope response for detected language
        # Default to "en" if language is unknown or not in the dict
        oos_lang = detected_language if detected_language in self.deployment_config.out_of_scope_response else "en"
        oos_response = self.deployment_config.out_of_scope_response.get(
            oos_lang,
            self.deployment_config.out_of_scope_response.get("en", "I'm sorry, I can't help with that.")
        )
        
        # Render the deployment block
        deployment_block = f"""# Deployment context
You are deployed at: {self.deployment_config.location_name}.
Your role here: {deployment_type_desc}

## What is out of scope
If asked something out of scope, respond with:
"{oos_response}\""""
        
        return deployment_block
    
    def _get_route_context_block(self, route_type: str, retrieved_context: str = "") -> str:
        """
        Get the route context block for the specified route type.
        
        Args:
            route_type: The route type ("general", "environment", "web_search", etc.)
            retrieved_context: Retrieved context to inject (for environment/web_search routes)
            
        Returns:
            Route context block as a string
        """
        # Route context templates
        ROUTE_CONTEXTS = {
            "general": (
                "# Task\n"
                "Answer the user's question directly and conversationally.\n"
                "Keep it brief — 1-3 sentences for voice responses."
            ),
            "environment": (
                "# Task\n"
                "Answer the user's question using the provided context about this location.\n"
                "Keep it brief — 1-3 sentences for voice responses.\n\n"
                "## Retrieved Context\n"
                "{retrieved_chunks}"
            ),
            "web_search": (
                "# Task\n"
                "Answer the user's question using the search results provided.\n"
                "Keep it brief — 1-3 sentences for voice responses.\n\n"
                "## Search Results\n"
                "{search_results_summary}"
            ),
            "clarify": (
                "# Task\n"
                "The user asked for a location that does not exist in this building.\n"
                "Apologise briefly in one sentence, then suggest 2-3 of the most relevant "
                "destinations from the Available destinations list.\n"
                "Do NOT suggest any location not in that list.\n"
                "Keep the response to 2 sentences maximum."
            ),
        }
        
        # Get the template for this route type, default to general
        template = ROUTE_CONTEXTS.get(route_type, ROUTE_CONTEXTS["general"])
        
        # Substitute retrieved context if applicable
        if route_type == "environment" and retrieved_context:
            return template.replace("{retrieved_chunks}", retrieved_context)
        elif route_type == "web_search" and retrieved_context:
            return template.replace("{search_results_summary}", retrieved_context)
        else:
            return template
    
    def _select_language_block(self, detected_language: str) -> str:
        """
        Select the appropriate language block based on detected language.
        
        Args:
            detected_language: The detected language code ("ja", "en", or "unknown")
            
        Returns:
            Language block content as a string
        """
        # Handle formal tone override for Japanese
        if detected_language == "ja" and self.deployment_config.tone_override == "formal":
            return self._prompt_cache["lang_ja_formal.txt"]
        
        # Map language to file
        language_files = {
            "ja": "lang_ja.txt",
            "en": "lang_en.txt",
            "ko": "lang_ko.txt",
            "zh": "lang_zh.txt",
            "unknown": "lang_unknown.txt",
        }
        
        # Get the appropriate file, default to unknown if language not recognized
        filename = language_files.get(detected_language, "lang_unknown.txt")
        return self._prompt_cache[filename]
    
    def _trim_history(self, session_history: list[dict]) -> list[dict]:
        """
        Trim session history to fit within turn count and token budget.
        
        Args:
            session_history: List of message dicts with "role" and "content" keys
            
        Returns:
            Trimmed history list (most recent turns, within token budget)
        """
        # First, limit to most recent session_memory_turns pairs
        max_messages = self.deployment_config.session_memory_turns * 2
        if len(session_history) > max_messages:
            session_history = session_history[-max_messages:]
        
        # Enforce 600 token hard cap
        # Estimate tokens as word count (simple approximation)
        TOKEN_BUDGET = 600
        
        def estimate_tokens(text: str) -> int:
            """Estimate token count as word count."""
            return len(text.split())
        
        # Calculate total tokens
        total_tokens = sum(estimate_tokens(msg["content"]) for msg in session_history)
        
        # Trim oldest pairs until under budget
        while total_tokens > TOKEN_BUDGET and len(session_history) >= 2:
            # Remove oldest user/assistant pair (first 2 messages)
            removed = session_history[:2]
            session_history = session_history[2:]
            total_tokens -= sum(estimate_tokens(msg["content"]) for msg in removed)
        
        return session_history
    
    def assemble_prompt(
        self,
        user_input: str,
        intent_result,  # IntentResult type (avoiding circular import)
        session_history: list[dict],
        retrieved_context: str = "",
        route_type: str = "general",
        building_context: dict | None = None,
    ) -> tuple[str, list[dict]]:
        """
        Assemble the complete system prompt and messages array.
        
        This method combines all prompt blocks in the defined order and builds
        the OpenAI-compatible messages list ready for the LLM call.
        
        Args:
            user_input: The current user message/transcript
            intent_result: IntentResult containing detected language and intent info
            session_history: List of previous user/assistant message dicts
            retrieved_context: Retrieved context for environment/web_search routes
            route_type: The selected route type ("general", "environment", "web_search", etc.)
            building_context: Optional building context dict injected as an extra prompt block.
                Contains ``current_node_label``, ``available_pois``, and ``floor_name``.
                When provided, a building context block is inserted between the deployment
                block and the language block.
            
        Returns:
            Tuple of (system_prompt: str, messages: list[dict]) where:
            - system_prompt is the assembled block string
            - messages is the OpenAI-compatible messages array with system, history, and user input
        """
        # Get the base block (model-tier specific)
        base_file = "base_small.txt" if self.model_tier == "small" else "base.txt"
        base_block = self._prompt_cache[base_file]
        
        # Render the deployment block
        deployment_block = self._render_deployment_block(intent_result.language)
        
        # Select the language block
        language_block = self._select_language_block(intent_result.language)
        
        # Get the route context block
        route_context_block = self._get_route_context_block(route_type, retrieved_context)

        # -----------------------------------------------------------------------
        # Block assembly order (each block is joined with a double newline):
        #
        #  1. base block       — Core persona and universal behaviour rules.
        #  2. deployment block — Location-specific identity, role, OOS response.
        #  3. building context — (optional) Current location, floor, available POIs.
        #                        Injected here so the LLM knows what destinations
        #                        exist before receiving language/tone instructions.
        #  4. language block   — Language and tone instructions for detected language.
        #  5. route context    — Task-specific instructions and retrieved context.
        # -----------------------------------------------------------------------
        blocks = [base_block, deployment_block]
        if building_context:
            blocks.append(_render_building_context_block(building_context))
        blocks.extend([language_block, route_context_block])

        system_prompt = "\n\n".join(blocks)
        
        # Build messages array
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Trim and append history
        trimmed_history = self._trim_history(session_history.copy())
        messages.extend(trimmed_history)
        
        # Append current user input
        messages.append({"role": "user", "content": user_input})
        
        return (system_prompt, messages)
