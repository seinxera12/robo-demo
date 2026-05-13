"""
Unit tests for the PostProcessor module.

Tests markdown removal, parenthetical removal, whitespace collapsing,
and Japanese CJK character spacing.
"""

import pytest
from server.llm.postprocess import PostProcessor


@pytest.fixture
def processor():
    """Create a PostProcessor instance for testing."""
    return PostProcessor()


def test_clean_empty_string(processor):
    """Test cleaning an empty string returns empty string."""
    result = processor.clean("", "en")
    assert result == ""


def test_clean_whitespace_only(processor):
    """Test cleaning whitespace-only string returns empty string."""
    result = processor.clean("   \n\t  ", "en")
    assert result == ""


def test_remove_markdown_asterisks(processor):
    """Test removal of asterisks used for bold/italic."""
    text = "This is *bold* and **very bold** text."
    result = processor.clean(text, "en")
    assert "*" not in result
    assert result == "This is bold and very bold text."


def test_remove_markdown_underscores(processor):
    """Test removal of underscores used for emphasis."""
    text = "This is _italic_ and __underlined__ text."
    result = processor.clean(text, "en")
    assert "_" not in result
    assert result == "This is italic and underlined text."


def test_remove_markdown_backticks(processor):
    """Test removal of backticks used for code."""
    text = "Use the `print()` function or ```code block```."
    result = processor.clean(text, "en")
    assert "`" not in result
    assert result == "Use the print() function or code block."


def test_remove_markdown_hashes(processor):
    """Test removal of hashes used for headers."""
    text = "# Heading 1\n## Heading 2\n### Heading 3"
    result = processor.clean(text, "en")
    assert "#" not in result
    assert result == "Heading 1 Heading 2 Heading 3"


def test_remove_markdown_angle_brackets(processor):
    """Test removal of angle brackets used for blockquotes."""
    text = "> This is a quote\n> Another line"
    result = processor.clean(text, "en")
    assert ">" not in result
    assert result == "This is a quote Another line"


def test_remove_markdown_hyphens(processor):
    """Test removal of hyphens used for lists."""
    text = "- Item 1\n- Item 2\n- Item 3"
    result = processor.clean(text, "en")
    assert "-" not in result
    assert result == "Item 1 Item 2 Item 3"


def test_remove_all_markdown_symbols(processor):
    """Test removal of all markdown symbols together."""
    text = "# Title\n*bold* _italic_ `code` > quote - list"
    result = processor.clean(text, "en")
    assert "*" not in result
    assert "_" not in result
    assert "`" not in result
    assert "#" not in result
    assert ">" not in result
    assert "-" not in result
    assert result == "Title bold italic code quote list"


def test_remove_parenthetical_asides(processor):
    """Test removal of text in parentheses."""
    text = "This is the main text (this is an aside) and more text."
    result = processor.clean(text, "en")
    assert "(" not in result
    assert ")" not in result
    assert "aside" not in result
    assert result == "This is the main text and more text."


def test_remove_multiple_parenthetical_asides(processor):
    """Test removal of multiple parenthetical asides."""
    text = "Text (aside 1) more text (aside 2) final text (aside 3)."
    result = processor.clean(text, "en")
    assert "aside" not in result
    assert result == "Text more text final text ."


def test_remove_nested_parentheses_content(processor):
    """Test removal of parenthetical content (non-nested regex)."""
    text = "Main text (outer content) more text."
    result = processor.clean(text, "en")
    assert "outer" not in result
    assert result == "Main text more text."


def test_collapse_multiple_spaces(processor):
    """Test collapsing multiple consecutive spaces."""
    text = "This  has   multiple    spaces."
    result = processor.clean(text, "en")
    assert result == "This has multiple spaces."


def test_collapse_multiple_newlines(processor):
    """Test collapsing multiple newlines into single space."""
    text = "Line 1\n\n\nLine 2\n\nLine 3"
    result = processor.clean(text, "en")
    assert result == "Line 1 Line 2 Line 3"


def test_collapse_mixed_whitespace(processor):
    """Test collapsing mixed whitespace (spaces, tabs, newlines)."""
    text = "Text  \t\n  more   \n\n  text"
    result = processor.clean(text, "en")
    assert result == "Text more text"


def test_strip_leading_trailing_whitespace(processor):
    """Test stripping leading and trailing whitespace."""
    text = "   Text with spaces around   "
    result = processor.clean(text, "en")
    assert result == "Text with spaces around"


def test_japanese_remove_spaces_between_cjk(processor):
    """Test removal of spaces between consecutive CJK characters in Japanese."""
    text = "これ は 日本語 の テスト です"
    result = processor.clean(text, "ja")
    # Spaces between CJK characters should be removed
    assert result == "これは日本語のテストです"


def test_japanese_preserve_spaces_with_non_cjk(processor):
    """Test that spaces with non-CJK characters are preserved in Japanese."""
    text = "日本語 and English 混在"
    result = processor.clean(text, "ja")
    # Spaces between CJK and non-CJK should be preserved
    assert "and" in result
    assert "English" in result


def test_japanese_hiragana_katakana_spacing(processor):
    """Test CJK spacing with hiragana and katakana."""
    text = "ひらがな カタカナ 漢字"
    result = processor.clean(text, "ja")
    # All are in CJK range, spaces should be removed
    assert result == "ひらがなカタカナ漢字"


def test_english_preserves_normal_spacing(processor):
    """Test that English text preserves normal word spacing."""
    text = "This is a normal English sentence."
    result = processor.clean(text, "en")
    assert result == "This is a normal English sentence."


def test_english_no_cjk_processing(processor):
    """Test that English mode doesn't apply CJK spacing rules."""
    text = "Some text with spaces"
    result = processor.clean(text, "en")
    assert result == "Some text with spaces"


def test_unknown_language_no_cjk_processing(processor):
    """Test that unknown language doesn't apply CJK spacing rules."""
    text = "これ は テスト"
    result = processor.clean(text, "unknown")
    # CJK spacing should NOT be applied for unknown language
    assert " " in result


def test_combined_markdown_and_parentheses(processor):
    """Test combined markdown and parenthetical removal."""
    text = "# Title\n*Bold text* (aside) and `code`."
    result = processor.clean(text, "en")
    assert "#" not in result
    assert "*" not in result
    assert "`" not in result
    assert "aside" not in result
    assert result == "Title Bold text and code."


def test_combined_all_cleaning_steps_english(processor):
    """Test all cleaning steps combined for English."""
    text = "# Heading\n*Bold* (aside) with  multiple   spaces\nand newlines."
    result = processor.clean(text, "en")
    assert result == "Heading Bold with multiple spaces and newlines."


def test_combined_all_cleaning_steps_japanese(processor):
    """Test all cleaning steps combined for Japanese."""
    text = "# 見出し\n*太字* (補足) これ は テスト  です"
    result = processor.clean(text, "ja")
    # Should remove markdown, parentheses, collapse whitespace, and remove CJK spacing
    assert "#" not in result
    assert "*" not in result
    assert "補足" not in result
    assert result == "見出し太字これはテストです"


def test_real_world_llm_output_english(processor):
    """Test realistic LLM output with markdown formatting."""
    text = """
    # Response
    
    Here's the answer: **important point** (note: this is extra info).
    
    You can use `this_function()` to achieve that.
    """
    result = processor.clean(text, "en")
    assert "#" not in result
    assert "*" not in result
    assert "`" not in result
    assert "note:" not in result
    assert "Response" in result
    assert "important point" in result
    # Backticks are removed, so underscores in function names become thisfunction
    assert "thisfunction()" in result


def test_real_world_llm_output_japanese(processor):
    """Test realistic Japanese LLM output."""
    text = "**重要**: これ は 回答 です (補足情報)。"
    result = processor.clean(text, "ja")
    assert "*" not in result
    assert "補足情報" not in result
    # The colon and space after it are preserved (: is not a CJK character)
    assert result == "重要: これは回答です。"


def test_empty_parentheses(processor):
    """Test that empty parentheses are preserved (for function names)."""
    text = "Text with () empty parens."
    result = processor.clean(text, "en")
    # Empty parentheses () are preserved (they might be part of function names)
    assert result == "Text with () empty parens."


def test_multiple_markdown_symbols_in_sequence(processor):
    """Test removal of multiple markdown symbols in sequence."""
    text = "***Very bold*** and ___very italic___"
    result = processor.clean(text, "en")
    assert "*" not in result
    assert "_" not in result
    assert result == "Very bold and very italic"


def test_markdown_at_start_and_end(processor):
    """Test markdown symbols at start and end of text."""
    text = "*Start* middle *end*"
    result = processor.clean(text, "en")
    assert result == "Start middle end"


def test_only_markdown_symbols(processor):
    """Test text consisting only of markdown symbols."""
    text = "***___```###"
    result = processor.clean(text, "en")
    assert result == ""


def test_only_parentheses(processor):
    """Test text consisting only of parenthetical content."""
    text = "(all content is in parentheses)"
    result = processor.clean(text, "en")
    assert result == ""


def test_japanese_punctuation_preserved(processor):
    """Test that Japanese punctuation is preserved."""
    text = "これ は テスト です。"
    result = processor.clean(text, "ja")
    assert "。" in result
    assert result == "これはテストです。"


def test_mixed_language_content(processor):
    """Test mixed Japanese and English content."""
    text = "日本語 text with English words 混在"
    result = processor.clean(text, "ja")
    # Should preserve spaces around English words
    assert "text" in result
    assert "English" in result
    assert "words" in result
