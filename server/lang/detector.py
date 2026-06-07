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
        lang = result.language.strip().lower()
        return lang if lang in _SUPPORTED else _DEFAULT
