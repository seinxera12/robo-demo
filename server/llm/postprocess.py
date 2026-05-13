"""
PostProcessor module for cleaning LLM output before TTS.

This module removes markdown symbols, parenthetical asides, and excess whitespace
from LLM responses to ensure natural-sounding TTS output.
"""

import re


class PostProcessor:
    """
    Cleans LLM output text for TTS consumption.
    
    Removes markdown formatting, parenthetical asides, and normalizes whitespace.
    For Japanese text, also removes spaces between CJK characters.
    """
    
    def clean(self, text: str, lang: str) -> str:
        """
        Clean LLM output text for TTS.
        
        Args:
            text: The raw LLM output text to clean
            lang: The detected language code ("ja", "en", "unknown")
        
        Returns:
            Cleaned text suitable for TTS synthesis
        
        Processing steps:
        1. Remove markdown symbols: * _ ` # > -
        2. Remove parenthetical asides: (...)
        3. Collapse whitespace: multiple spaces/newlines → single space, strip
        4. For lang == "ja": remove spaces between consecutive CJK characters (U+3000–U+9FFF)
        """
        if not text:
            return ""
        
        # Step 1: Remove markdown symbols
        # Remove: * _ ` # > -
        markdown_symbols = ['*', '_', '`', '#', '>', '-']
        for symbol in markdown_symbols:
            text = text.replace(symbol, '')
        
        # Step 2: Remove parenthetical asides
        # Match opening ( and closing ) with any content in between
        # But preserve empty parentheses () which might be part of function names
        text = re.sub(r'\([^)]+\)', '', text)
        
        # Step 3: Collapse whitespace
        # Replace multiple spaces/newlines with single space
        text = re.sub(r'\s+', ' ', text)
        # Strip leading and trailing whitespace
        text = text.strip()
        
        # Step 4: For Japanese, remove spaces between consecutive CJK characters
        if lang == "ja":
            # CJK Unified Ideographs range: U+3000–U+9FFF
            # This pattern matches: CJK character + space(s) + CJK character
            # and replaces with: CJK character + CJK character (no space)
            # Apply repeatedly to handle multiple consecutive CJK characters with spaces
            while re.search(r'([\u3000-\u9FFF])\s+([\u3000-\u9FFF])', text):
                text = re.sub(r'([\u3000-\u9FFF])\s+([\u3000-\u9FFF])', r'\1\2', text)
        
        return text
