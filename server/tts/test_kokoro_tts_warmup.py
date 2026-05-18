"""
Unit tests for KokoroTTS.warm_up() and KokoroJapaneseTTS.warm_up().

Property 8: warm_up loads pipeline without producing audio.
Validates: Requirements 3.4, 3.5
"""

from unittest.mock import MagicMock, patch, call
import pytest

from server.tts.kokoro_tts import KokoroTTS, KokoroJapaneseTTS


# ---------------------------------------------------------------------------
# KokoroTTS.warm_up()
# ---------------------------------------------------------------------------

class TestKokoroTTSWarmUp:
    """Tests for KokoroTTS.warm_up()."""

    def test_warm_up_calls_get_pipeline_exactly_once(self):
        """warm_up() must call _get_pipeline exactly once."""
        tts = KokoroTTS()
        mock_pipeline = MagicMock()
        with patch.object(tts, "_get_pipeline", return_value=mock_pipeline) as mock_get:
            tts.warm_up()
        mock_get.assert_called_once_with()

    def test_warm_up_returns_none(self):
        """warm_up() must return None — no audio bytes produced."""
        tts = KokoroTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()):
            result = tts.warm_up()
        assert result is None

    def test_warm_up_sets_pipeline_non_none(self):
        """After warm_up(), _pipeline must be non-None."""
        tts = KokoroTTS()
        assert tts._pipeline is None  # starts unloaded

        fake_pipeline = MagicMock()

        def _fake_get_pipeline():
            tts._pipeline = fake_pipeline
            return fake_pipeline

        with patch.object(tts, "_get_pipeline", side_effect=_fake_get_pipeline):
            tts.warm_up()

        assert tts._pipeline is not None

    def test_warm_up_does_not_call_synthesize_sync(self):
        """warm_up() must not invoke _synthesize_sync (no audio produced)."""
        tts = KokoroTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()), \
             patch.object(tts, "_synthesize_sync") as mock_synth:
            tts.warm_up()
        mock_synth.assert_not_called()

    def test_warm_up_logs_warm_up_complete(self):
        """warm_up() must log 'warm_up_complete  engine=KokoroTTS  voice=...'."""
        tts = KokoroTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()), \
             patch("server.tts.kokoro_tts.tts_log") as mock_log:
            tts.warm_up()
        mock_log.info.assert_called_once_with(
            "warm_up_complete  engine=KokoroTTS  voice=%s", KokoroTTS.DEFAULT_VOICE
        )

    def test_warm_up_calls_load_voice(self):
        """warm_up() must call load_voice(DEFAULT_VOICE) to pre-cache the voice file."""
        tts = KokoroTTS()
        mock_pipeline = MagicMock()
        with patch.object(tts, "_get_pipeline", return_value=mock_pipeline):
            tts.warm_up()
        mock_pipeline.load_voice.assert_called_once_with(KokoroTTS.DEFAULT_VOICE)

    def test_warm_up_idempotent_calls_get_pipeline_each_time(self):
        """Calling warm_up() twice calls _get_pipeline twice (idempotency is
        handled inside _get_pipeline itself via the None guard)."""
        tts = KokoroTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()) as mock_get:
            tts.warm_up()
            tts.warm_up()
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# KokoroJapaneseTTS.warm_up()
# ---------------------------------------------------------------------------

class TestKokoroJapaneseTTSWarmUp:
    """Tests for KokoroJapaneseTTS.warm_up()."""

    def test_warm_up_calls_get_pipeline_exactly_once(self):
        """warm_up() must call _get_pipeline exactly once."""
        tts = KokoroJapaneseTTS()
        mock_pipeline = MagicMock()
        with patch.object(tts, "_get_pipeline", return_value=mock_pipeline) as mock_get:
            tts.warm_up()
        mock_get.assert_called_once_with()

    def test_warm_up_returns_none(self):
        """warm_up() must return None — no audio bytes produced."""
        tts = KokoroJapaneseTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()):
            result = tts.warm_up()
        assert result is None

    def test_warm_up_sets_pipeline_non_none(self):
        """After warm_up(), _pipeline must be non-None."""
        tts = KokoroJapaneseTTS()
        assert tts._pipeline is None  # starts unloaded

        fake_pipeline = MagicMock()

        def _fake_get_pipeline():
            tts._pipeline = fake_pipeline
            return fake_pipeline

        with patch.object(tts, "_get_pipeline", side_effect=_fake_get_pipeline):
            tts.warm_up()

        assert tts._pipeline is not None

    def test_warm_up_does_not_call_synthesize_sync(self):
        """warm_up() must not invoke _synthesize_sync (no audio produced)."""
        tts = KokoroJapaneseTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()), \
             patch.object(tts, "_synthesize_sync") as mock_synth:
            tts.warm_up()
        mock_synth.assert_not_called()

    def test_warm_up_logs_warm_up_complete(self):
        """warm_up() must log 'warm_up_complete  engine=KokoroJapaneseTTS  voice=...'."""
        tts = KokoroJapaneseTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()), \
             patch("server.tts.kokoro_tts.tts_log") as mock_log:
            tts.warm_up()
        mock_log.info.assert_called_once_with(
            "warm_up_complete  engine=KokoroJapaneseTTS  voice=%s", KokoroJapaneseTTS.DEFAULT_VOICE
        )

    def test_warm_up_calls_load_voice(self):
        """warm_up() must call load_voice(DEFAULT_VOICE) to pre-cache the voice file."""
        tts = KokoroJapaneseTTS()
        mock_pipeline = MagicMock()
        with patch.object(tts, "_get_pipeline", return_value=mock_pipeline):
            tts.warm_up()
        mock_pipeline.load_voice.assert_called_once_with(KokoroJapaneseTTS.DEFAULT_VOICE)

    def test_warm_up_idempotent_calls_get_pipeline_each_time(self):
        """Calling warm_up() twice calls _get_pipeline twice."""
        tts = KokoroJapaneseTTS()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()) as mock_get:
            tts.warm_up()
            tts.warm_up()
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# Property 8: warm_up loads pipeline without producing audio (parametrised)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls,engine_name", [
    (KokoroTTS, "KokoroTTS"),
    (KokoroJapaneseTTS, "KokoroJapaneseTTS"),
])
class TestProperty8WarmUpNoPipeline:
    """
    Property 8: warm_up loads pipeline without producing audio.
    Validates: Requirements 3.4, 3.5
    """

    def test_warm_up_returns_none_not_bytes(self, cls, engine_name):
        """warm_up() SHALL NOT return any audio bytes."""
        tts = cls()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()):
            result = tts.warm_up()
        assert result is None, (
            f"{engine_name}.warm_up() returned {result!r}, expected None"
        )

    def test_pipeline_loaded_after_warm_up(self, cls, engine_name):
        """_pipeline SHALL be non-None after warm_up() completes."""
        tts = cls()
        fake_pipeline = MagicMock()

        def _set_and_return():
            tts._pipeline = fake_pipeline
            return fake_pipeline

        with patch.object(tts, "_get_pipeline", side_effect=_set_and_return):
            tts.warm_up()

        assert tts._pipeline is not None, (
            f"{engine_name}._pipeline is still None after warm_up()"
        )

    def test_get_pipeline_called_not_synthesize(self, cls, engine_name):
        """warm_up() SHALL call _get_pipeline and SHALL NOT call _synthesize_sync."""
        tts = cls()
        with patch.object(tts, "_get_pipeline", return_value=MagicMock()) as mock_get, \
             patch.object(tts, "_synthesize_sync") as mock_synth:
            tts.warm_up()
        mock_get.assert_called_once()
        mock_synth.assert_not_called()
