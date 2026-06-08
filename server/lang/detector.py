"""
Language detection for the Lightweight Voice Demo.

Reads the `language` field from a TranscriptionResult (provided by the Groq
Whisper API) and maps it to a supported language code.  No external model is
required — Groq Whisper performs language detection automatically.
"""

from __future__ import annotations

from server.models import TranscriptionResult

# Languages supported by the TTS router.
_SUPPORTED: frozenset[str] = frozenset({"en", "ja", "ko", "zh"})
_DEFAULT: str = "en"

# ISO 639-2 and common variant codes → ISO 639-1 normalisation map.
# Whisper may return either form; always normalise before returning to callers.
_NORMALISE_MAP: dict[str, str] = {
    # ISO 639-2 alphabetic codes
    "eng": "en", "jpn": "ja", "kor": "ko",
    "zho": "zh", "cmn": "zh", "chi": "zh",
    # BCP-47 region variants
    "zh-cn": "zh", "zh-tw": "zh",
    "ja-jp": "ja", "ko-kr": "ko",
    "en-us": "en", "en-gb": "en",
    "en-au": "en", "en-ca": "en",
}


def normalise_language(lang: str) -> str:
    """Normalise a raw Whisper language code to a supported ISO 639-1 code.

    Applies _NORMALISE_MAP first (ISO 639-2 / BCP-47 variants), then falls
    back to _DEFAULT ("en") for any still-unrecognised code.

    Args:
        lang: Raw language string from Whisper (e.g. "eng", "en", "zh-cn").

    Returns:
        One of "en", "ja", "ko", "zh".
    """
    code = lang.strip().lower()
    code = _NORMALISE_MAP.get(code, code)
    return code if code in _SUPPORTED else _DEFAULT


class LanguageDetector:
    """Maps a TranscriptionResult's language field to a supported language code.

    Supported codes: ``"en"`` (English), ``"ja"`` (Japanese),
    ``"ko"`` (Korean), and ``"zh"`` (Mandarin Chinese).
    Any other value — including an empty string — falls back to ``"en"``.
    """

    def detect(self, result: TranscriptionResult) -> str:
        """Return the detected language code for *result*.

        Parameters
        ----------
        result:
            A :class:`~server.models.TranscriptionResult` produced by the STT
            backend.  The ``language`` field is expected to be an ISO 639-1
            code such as ``"en"`` or ``"ja"``, but may be any string.

        Returns
        -------
        str
            ``"en"``, ``"ja"``, ``"ko"``, or ``"zh"``.  Defaults to ``"en"``
            for all unrecognised or empty codes.
        """
        return normalise_language(result.language)
