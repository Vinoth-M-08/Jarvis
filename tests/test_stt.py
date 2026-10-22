# jarvis/tests/test_stt.py
import pytest
import numpy as np
from unittest.mock import patch, MagicMock

STUB_CONFIG = {
    "stt": {"model": "turbo", "device": "cuda", "compute_type": "float16"}
}

def test_transcribe_returns_string():
    """transcribe_audio should return a string for valid audio."""
    with patch("jarvis.stt._load_config", return_value=STUB_CONFIG), \
         patch("jarvis.stt.WhisperModel") as mock_model_cls:
        mock_model = MagicMock()
        mock_segment = MagicMock()
        mock_segment.text = " hello world"
        mock_model.transcribe.return_value = ([mock_segment], None)
        mock_model_cls.return_value = mock_model
        # Reset module singleton so new mock takes effect
        import jarvis.stt as stt_mod
        stt_mod._model = None

        from jarvis.stt import transcribe_audio
        fake_audio = np.zeros(16000, dtype=np.float32)
        result = transcribe_audio(fake_audio)
        assert isinstance(result, str)

def test_transcribe_strips_whitespace():
    """transcribe_audio should return clean stripped text."""
    with patch("jarvis.stt._load_config", return_value=STUB_CONFIG), \
         patch("jarvis.stt.WhisperModel") as mock_model_cls:
        mock_model = MagicMock()
        mock_segment = MagicMock()
        mock_segment.text = "  hello world  "
        mock_model.transcribe.return_value = ([mock_segment], None)
        mock_model_cls.return_value = mock_model
        import jarvis.stt as stt_mod
        stt_mod._model = None

        from jarvis.stt import transcribe_audio
        fake_audio = np.zeros(16000, dtype=np.float32)
        result = transcribe_audio(fake_audio)
        assert result == "hello world"

def test_transcribe_concatenates_segments():
    """transcribe_audio should join multiple segments."""
    with patch("jarvis.stt._load_config", return_value=STUB_CONFIG), \
         patch("jarvis.stt.WhisperModel") as mock_model_cls:
        mock_model = MagicMock()
        seg1 = MagicMock(); seg1.text = "hello"
        seg2 = MagicMock(); seg2.text = " world"
        mock_model.transcribe.return_value = ([seg1, seg2], None)
        mock_model_cls.return_value = mock_model
        import jarvis.stt as stt_mod
        stt_mod._model = None

        from jarvis.stt import transcribe_audio
        result = transcribe_audio(np.zeros(16000, dtype=np.float32))
        assert result == "hello world"


def test_has_speech_ignores_silence_and_room_noise():
    """Silence must not reach Whisper (it hallucinates "You" / "Thank you")."""
    from jarvis.stt import has_speech
    rng = np.random.default_rng(0)
    assert not has_speech(np.zeros(4800, dtype=np.float32))            # empty 0.3 s (aborted recording)
    assert not has_speech(np.zeros(0, dtype=np.float32))
    assert not has_speech((rng.standard_normal(32000) * 0.002).astype(np.float32))  # quiet room


def test_has_speech_detects_a_spoken_chunk():
    from jarvis.stt import has_speech
    audio = np.zeros(32000, dtype=np.float32)
    t = np.arange(4800) / 16000
    audio[9600:14400] = 0.1 * np.sin(2 * np.pi * 220 * t)              # 0.3 s of voice-level sound
    assert has_speech(audio)
