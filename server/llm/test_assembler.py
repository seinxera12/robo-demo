"""
Unit tests for the DeploymentConfig dataclass.
"""

import pytest
from pathlib import Path
import tempfile
import yaml
from dataclasses import dataclass

from server.llm.assembler import DeploymentConfig, detect_model_tier, PromptAssembler


# Mock IntentResult to avoid importing dependencies in tests
@dataclass
class MockIntentResult:
    """Mock IntentResult for testing without full dependency chain."""
    intent: str
    language: str
    confidence: float
    needs_clarification: bool
    clarification_reason: str | None
    query_clean: str


def test_deployment_config_default():
    """Test that default() creates a config with expected default values."""
    config = DeploymentConfig.default()
    
    assert config.deployment_id == "default"
    assert config.deployment_type == "desktop"
    assert config.location_name == "Robo Assistant"
    assert config.language_primary == "en"
    assert config.language_secondary == "ja"
    assert config.tone_override is None
    assert config.environment_docs == []
    assert config.session_memory_turns == 6
    assert config.web_search_enabled is False
    assert "ja" in config.out_of_scope_response
    assert "en" in config.out_of_scope_response


def test_deployment_config_from_yaml_valid():
    """Test loading a valid deployment.yaml file."""
    yaml_content = """
deployment_id: "test-deployment"
deployment_type: "reception"
location_name: "Test Location"
language_primary: "ja"
language_secondary: "en"
tone_override: "formal"
environment_docs:
  - path: "docs/test.md"
session_memory_turns: 10
web_search_enabled: true
out_of_scope_response:
  ja: "テストメッセージ"
  en: "Test message"
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        f.write(yaml_content)
        temp_path = f.name
    
    try:
        config = DeploymentConfig.from_yaml(temp_path)
        
        assert config.deployment_id == "test-deployment"
        assert config.deployment_type == "reception"
        assert config.location_name == "Test Location"
        assert config.language_primary == "ja"
        assert config.language_secondary == "en"
        assert config.tone_override == "formal"
        assert len(config.environment_docs) == 1
        assert config.environment_docs[0]["path"] == "docs/test.md"
        assert config.session_memory_turns == 10
        assert config.web_search_enabled is True
        assert config.out_of_scope_response["ja"] == "テストメッセージ"
        assert config.out_of_scope_response["en"] == "Test message"
    finally:
        Path(temp_path).unlink()


def test_deployment_config_from_yaml_partial():
    """Test loading a YAML file with only some fields specified."""
    yaml_content = """
deployment_id: "partial-test"
location_name: "Partial Location"
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        f.write(yaml_content)
        temp_path = f.name
    
    try:
        config = DeploymentConfig.from_yaml(temp_path)
        
        # Specified fields
        assert config.deployment_id == "partial-test"
        assert config.location_name == "Partial Location"
        
        # Default fields
        assert config.deployment_type == "desktop"
        assert config.language_primary == "en"
        assert config.session_memory_turns == 6
        assert config.web_search_enabled is False
    finally:
        Path(temp_path).unlink()


def test_deployment_config_from_yaml_empty():
    """Test loading an empty YAML file uses all defaults."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
        f.write("")
        temp_path = f.name
    
    try:
        config = DeploymentConfig.from_yaml(temp_path)
        
        # Should match default config
        default_config = DeploymentConfig.default()
        assert config.deployment_id == default_config.deployment_id
        assert config.deployment_type == default_config.deployment_type
        assert config.location_name == default_config.location_name
    finally:
        Path(temp_path).unlink()


def test_deployment_config_from_yaml_missing_file():
    """Test that from_yaml raises FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError, match="Deployment config file not found"):
        DeploymentConfig.from_yaml("nonexistent/path/to/config.yaml")


def test_deployment_config_from_yaml_actual_file():
    """Test loading the actual config/deployment.yaml file."""
    config_path = "config/deployment.yaml"
    
    if not Path(config_path).exists():
        pytest.skip(f"Config file {config_path} not found")
    
    config = DeploymentConfig.from_yaml(config_path)
    
    # Verify it loaded successfully and has expected types
    assert isinstance(config.deployment_id, str)
    assert isinstance(config.deployment_type, str)
    assert isinstance(config.location_name, str)
    assert isinstance(config.language_primary, str)
    assert isinstance(config.language_secondary, str)
    assert isinstance(config.environment_docs, list)
    assert isinstance(config.session_memory_turns, int)
    assert isinstance(config.web_search_enabled, bool)
    assert isinstance(config.out_of_scope_response, dict)


def test_detect_model_tier_llama():
    """Test that model names containing 'llama' are detected as groq tier."""
    assert detect_model_tier("llama-3.3-70b-versatile") == "groq"
    assert detect_model_tier("llama-3.1-8b") == "groq"
    assert detect_model_tier("LLAMA-2") == "groq"
    assert detect_model_tier("meta-llama-3") == "groq"


def test_detect_model_tier_gemini():
    """Test that model names containing 'gemini' are detected as groq tier."""
    assert detect_model_tier("gemini-pro") == "groq"
    assert detect_model_tier("GEMINI-1.5") == "groq"
    assert detect_model_tier("google-gemini-flash") == "groq"


def test_detect_model_tier_small():
    """Test that other model names are detected as small tier."""
    assert detect_model_tier("qwen2.5:7b") == "small"
    assert detect_model_tier("gpt-4") == "small"
    assert detect_model_tier("claude-3") == "small"
    assert detect_model_tier("mistral-7b") == "small"
    assert detect_model_tier("phi-3") == "small"


def test_detect_model_tier_case_insensitive():
    """Test that detection is case-insensitive."""
    assert detect_model_tier("LLAMA") == "groq"
    assert detect_model_tier("LLaMa") == "groq"
    assert detect_model_tier("llama") == "groq"
    assert detect_model_tier("GEMINI") == "groq"
    assert detect_model_tier("GeMiNi") == "groq"
    assert detect_model_tier("gemini") == "groq"


def test_detect_model_tier_edge_cases():
    """Test edge cases for model tier detection."""
    # Empty string should return small
    assert detect_model_tier("") == "small"
    
    # Partial matches should work
    assert detect_model_tier("my-llama-model") == "groq"
    assert detect_model_tier("gemini-based") == "groq"
    
    # Similar but not matching strings should return small
    assert detect_model_tier("lama") == "small"  # Missing one 'l'
    assert detect_model_tier("gemeni") == "small"  # Typo



def test_render_deployment_block_desktop():
    """Test rendering deployment block for desktop deployment type."""
    config = DeploymentConfig(
        deployment_type="desktop",
        location_name="My Computer",
        out_of_scope_response={
            "ja": "申し訳ありませんが、それについてはお答えできません。",
            "en": "I'm sorry, I can't help with that."
        }
    )
    
    # Create a temporary prompts directory with minimal required files
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create minimal required prompt files
        (prompts_path / "base.txt").write_text("Base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese prompt", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English prompt", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown prompt", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        # Test English
        block_en = assembler._render_deployment_block("en")
        assert "My Computer" in block_en
        assert "personal assistant on the user's computer" in block_en
        assert "I'm sorry, I can't help with that." in block_en
        
        # Test Japanese
        block_ja = assembler._render_deployment_block("ja")
        assert "My Computer" in block_ja
        assert "personal assistant on the user's computer" in block_ja
        assert "申し訳ありませんが、それについてはお答えできません。" in block_ja


def test_render_deployment_block_reception():
    """Test rendering deployment block for reception deployment type."""
    config = DeploymentConfig(
        deployment_type="reception",
        location_name="Main Building Lobby",
        out_of_scope_response={
            "ja": "それはできません。",
            "en": "I cannot help with that."
        }
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create minimal required prompt files
        (prompts_path / "base.txt").write_text("Base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese prompt", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English prompt", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown prompt", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        block = assembler._render_deployment_block("en")
        assert "Main Building Lobby" in block
        assert "help visitors and staff at the building entrance" in block
        assert "I cannot help with that." in block


def test_render_deployment_block_enterprise():
    """Test rendering deployment block for enterprise deployment type."""
    config = DeploymentConfig(
        deployment_type="enterprise",
        location_name="Acme Corp HQ",
        out_of_scope_response={
            "ja": "社内規定により対応できません。",
            "en": "I cannot assist with that per company policy."
        }
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create minimal required prompt files
        (prompts_path / "base.txt").write_text("Base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese prompt", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English prompt", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown prompt", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        block = assembler._render_deployment_block("en")
        assert "Acme Corp HQ" in block
        assert "assist employees with internal questions" in block
        assert "I cannot assist with that per company policy." in block


def test_render_deployment_block_unknown_language():
    """Test rendering deployment block with unknown language falls back to English."""
    config = DeploymentConfig(
        deployment_type="desktop",
        location_name="Test Location",
        out_of_scope_response={
            "ja": "日本語メッセージ",
            "en": "English message"
        }
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create minimal required prompt files
        (prompts_path / "base.txt").write_text("Base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese prompt", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English prompt", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown prompt", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        # Test with unknown language
        block = assembler._render_deployment_block("unknown")
        assert "Test Location" in block
        assert "English message" in block  # Should fall back to English
        
        # Test with language not in out_of_scope_response dict
        block_fr = assembler._render_deployment_block("fr")
        assert "Test Location" in block_fr
        assert "English message" in block_fr  # Should fall back to English


def test_render_deployment_block_unknown_deployment_type():
    """Test rendering deployment block with unknown deployment type falls back to desktop."""
    config = DeploymentConfig(
        deployment_type="unknown_type",
        location_name="Test Location",
        out_of_scope_response={
            "ja": "日本語",
            "en": "English"
        }
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create minimal required prompt files
        (prompts_path / "base.txt").write_text("Base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese prompt", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English prompt", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown prompt", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        block = assembler._render_deployment_block("en")
        assert "Test Location" in block
        # Should fall back to desktop description
        assert "personal assistant on the user's computer" in block


def test_render_deployment_block_structure():
    """Test that deployment block has the expected structure."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create minimal required prompt files
        (prompts_path / "base.txt").write_text("Base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese prompt", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English prompt", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown prompt", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        block = assembler._render_deployment_block("en")
        
        # Check for expected sections
        assert "# Deployment context" in block
        assert "You are deployed at:" in block
        assert "Your role here:" in block
        assert "## What is out of scope" in block
        assert "If asked something out of scope, respond with:" in block



def test_assemble_prompt_basic():
    """Test basic prompt assembly with minimal inputs."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base prompt content", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese language block", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English language block", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown language block", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="en",
            confidence=0.8,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="What is the weather?"
        )
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="What is the weather?",
            intent_result=intent_result,
            session_history=[],
            retrieved_context="",
            route_type="general"
        )
        
        # Verify system prompt contains all blocks
        assert "Base prompt content" in system_prompt
        assert "Deployment context" in system_prompt
        assert "English language block" in system_prompt
        assert "Answer the user's question directly" in system_prompt
        
        # Verify blocks are joined with double newlines
        assert "\n\n" in system_prompt
        
        # Verify messages structure
        assert len(messages) == 2  # system + user
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == system_prompt
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "What is the weather?"


def test_assemble_prompt_with_history():
    """Test prompt assembly with session history."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="en",
            confidence=0.8,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="Follow-up question"
        )
        
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well, thanks!"},
        ]
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="Follow-up question",
            intent_result=intent_result,
            session_history=history,
            retrieved_context="",
            route_type="general"
        )
        
        # Verify messages structure: system + history + user
        assert len(messages) == 6  # system + 4 history + user
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "Hi there!"
        assert messages[5]["role"] == "user"
        assert messages[5]["content"] == "Follow-up question"


def test_assemble_prompt_japanese():
    """Test prompt assembly with Japanese language."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("日本語ブロック", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="ja",
            confidence=0.9,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="天気はどうですか？"
        )
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="天気はどうですか？",
            intent_result=intent_result,
            session_history=[],
            retrieved_context="",
            route_type="general"
        )
        
        # Verify Japanese language block is included
        assert "日本語ブロック" in system_prompt
        assert "English" not in system_prompt or "English" in "Deployment context"  # English might appear in deployment block


def test_assemble_prompt_environment_route():
    """Test prompt assembly with environment route and retrieved context."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="environment",
            language="en",
            confidence=0.85,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="Where is the cafeteria?"
        )
        
        retrieved_context = "The cafeteria is on the 3rd floor, open 11am-2pm."
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="Where is the cafeteria?",
            intent_result=intent_result,
            session_history=[],
            retrieved_context=retrieved_context,
            route_type="environment"
        )
        
        # Verify environment route context is included
        assert "Answer the user's question using the provided context" in system_prompt
        assert "Retrieved Context" in system_prompt
        assert retrieved_context in system_prompt


def test_assemble_prompt_web_search_route():
    """Test prompt assembly with web search route and search results."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="web_search",
            language="en",
            confidence=0.9,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="Latest news about AI"
        )
        
        search_results = "Recent AI developments include new language models and robotics advances."
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="What's the latest news about AI?",
            intent_result=intent_result,
            session_history=[],
            retrieved_context=search_results,
            route_type="web_search"
        )
        
        # Verify web search route context is included
        assert "Answer the user's question using the search results" in system_prompt
        assert "Search Results" in system_prompt
        assert search_results in system_prompt


def test_assemble_prompt_small_model_tier():
    """Test prompt assembly uses base_small.txt for small model tier."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Full base prompt", encoding='utf-8')
        (prompts_path / "base_small.txt").write_text("Simplified base prompt", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="small"  # Use small tier
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="en",
            confidence=0.8,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="Test question"
        )
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="Test question",
            intent_result=intent_result,
            session_history=[],
            retrieved_context="",
            route_type="general"
        )
        
        # Verify small base prompt is used
        assert "Simplified base prompt" in system_prompt
        assert "Full base prompt" not in system_prompt


def test_assemble_prompt_history_trimming():
    """Test that history is trimmed to session_memory_turns."""
    config = DeploymentConfig(
        session_memory_turns=2  # Only keep 2 pairs
    )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="en",
            confidence=0.8,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="Current question"
        )
        
        # Create history with 5 pairs (10 messages)
        history = []
        for i in range(5):
            history.append({"role": "user", "content": f"Question {i+1}"})
            history.append({"role": "assistant", "content": f"Answer {i+1}"})
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="Current question",
            intent_result=intent_result,
            session_history=history,
            retrieved_context="",
            route_type="general"
        )
        
        # Should have: system + 2 pairs (4 messages) + current user = 6 messages
        assert len(messages) == 6
        
        # Verify it kept the most recent 2 pairs
        assert messages[1]["content"] == "Question 4"
        assert messages[2]["content"] == "Answer 4"
        assert messages[3]["content"] == "Question 5"
        assert messages[4]["content"] == "Answer 5"
        assert messages[5]["content"] == "Current question"


def test_assemble_prompt_block_order():
    """Test that blocks are assembled in the correct order."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files with unique markers
        (prompts_path / "base.txt").write_text("BLOCK_1_BASE", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("BLOCK_3_LANGUAGE", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("Unknown", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="en",
            confidence=0.8,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="Test"
        )
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="Test",
            intent_result=intent_result,
            session_history=[],
            retrieved_context="",
            route_type="general"
        )
        
        # Find positions of each block marker
        base_pos = system_prompt.find("BLOCK_1_BASE")
        deployment_pos = system_prompt.find("Deployment context")
        language_pos = system_prompt.find("BLOCK_3_LANGUAGE")
        route_pos = system_prompt.find("Answer the user's question")
        
        # Verify all blocks are present
        assert base_pos != -1
        assert deployment_pos != -1
        assert language_pos != -1
        assert route_pos != -1
        
        # Verify order: base < deployment < language < route
        assert base_pos < deployment_pos
        assert deployment_pos < language_pos
        assert language_pos < route_pos


def test_assemble_prompt_unknown_language():
    """Test prompt assembly with unknown language."""
    config = DeploymentConfig.default()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        prompts_path = Path(temp_dir)
        
        # Create required prompt files
        (prompts_path / "base.txt").write_text("Base", encoding='utf-8')
        (prompts_path / "lang_ja.txt").write_text("Japanese", encoding='utf-8')
        (prompts_path / "lang_en.txt").write_text("English", encoding='utf-8')
        (prompts_path / "lang_unknown.txt").write_text("UNKNOWN_LANGUAGE_BLOCK", encoding='utf-8')
        
        assembler = PromptAssembler(
            prompts_dir=str(prompts_path),
            deployment_config=config,
            model_tier="groq"
        )
        
        intent_result = MockIntentResult(
            intent="general",
            language="unknown",
            confidence=0.5,
            needs_clarification=False,
            clarification_reason=None,
            query_clean="???"
        )
        
        system_prompt, messages = assembler.assemble_prompt(
            user_input="???",
            intent_result=intent_result,
            session_history=[],
            retrieved_context="",
            route_type="general"
        )
        
        # Verify unknown language block is used
        assert "UNKNOWN_LANGUAGE_BLOCK" in system_prompt
