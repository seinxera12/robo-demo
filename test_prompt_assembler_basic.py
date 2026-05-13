"""
Quick test to verify PromptAssembler basic functionality.
"""

from server.llm.assembler import PromptAssembler, DeploymentConfig


def test_prompt_assembler_initialization():
    """Test that PromptAssembler initializes and loads files correctly."""
    config = DeploymentConfig.default()
    
    # Test with groq tier
    assembler_groq = PromptAssembler(
        prompts_dir="server/prompts",
        deployment_config=config,
        model_tier="groq"
    )
    
    # Verify cache contains expected files
    assert "base.txt" in assembler_groq._prompt_cache
    assert "lang_ja.txt" in assembler_groq._prompt_cache
    assert "lang_en.txt" in assembler_groq._prompt_cache
    assert "lang_unknown.txt" in assembler_groq._prompt_cache
    assert "lang_ja_formal.txt" in assembler_groq._prompt_cache
    
    # Verify content is loaded
    assert len(assembler_groq._prompt_cache["base.txt"]) > 0
    assert len(assembler_groq._prompt_cache["lang_ja.txt"]) > 0
    
    # Test with small tier
    assembler_small = PromptAssembler(
        prompts_dir="server/prompts",
        deployment_config=config,
        model_tier="small"
    )
    
    # Verify small tier uses base_small.txt
    assert "base_small.txt" in assembler_small._prompt_cache
    assert len(assembler_small._prompt_cache["base_small.txt"]) > 0


def test_prompt_assembler_missing_file():
    """Test that PromptAssembler raises FileNotFoundError for missing files."""
    import pytest
    
    config = DeploymentConfig.default()
    
    with pytest.raises(FileNotFoundError, match="Required prompt file not found"):
        PromptAssembler(
            prompts_dir="nonexistent/directory",
            deployment_config=config,
            model_tier="groq"
        )


def test_prompt_assembler_formal_fallback(caplog):
    """Test that PromptAssembler falls back to lang_ja.txt when lang_ja_formal.txt is missing."""
    import tempfile
    import shutil
    from pathlib import Path
    
    config = DeploymentConfig.default()
    
    # Create a temporary directory with only required files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        
        # Copy required files
        shutil.copy("server/prompts/base.txt", tmppath / "base.txt")
        shutil.copy("server/prompts/lang_ja.txt", tmppath / "lang_ja.txt")
        shutil.copy("server/prompts/lang_en.txt", tmppath / "lang_en.txt")
        shutil.copy("server/prompts/lang_unknown.txt", tmppath / "lang_unknown.txt")
        
        # Initialize assembler (lang_ja_formal.txt is missing)
        assembler = PromptAssembler(
            prompts_dir=str(tmppath),
            deployment_config=config,
            model_tier="groq"
        )
        
        # Verify fallback occurred
        assert "lang_ja_formal.txt" in assembler._prompt_cache
        assert assembler._prompt_cache["lang_ja_formal.txt"] == assembler._prompt_cache["lang_ja.txt"]
        
        # Verify warning was logged
        assert "falling back to lang_ja.txt" in caplog.text


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
