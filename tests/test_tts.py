# jarvis/tests/test_tts.py
import pytest
import numpy as np
from unittest.mock import patch, MagicMock, call

STUB_CONFIG = {
    "tts": {"voice": "af_heart", "speed": 1.1}
}

def test_tts_speak_returns_audio_bytes():
    """TTS should return audio bytes for valid text."""
    mock_audio = np.ones(100, dtype=np.float32) * 0.1
    with patch("jarvis.tts._get_pipeline") as mock_get_pipeline, \
         patch("jarvis.tts._load_config", return_value=STUB_CONFIG):
        mock_pipeline = MagicMock()
        mock_pipeline.return_value = iter([(None, None, mock_audio)])
        mock_get_pipeline.return_value = mock_pipeline
        from jarvis.tts import speak_to_bytes
        result = speak_to_bytes("Hello, sir.")
        assert isinstance(result, bytes)
        assert len(result) > 0

def test_tts_rejects_empty_string():
    """TTS should raise ValueError for empty input."""
    from jarvis.tts import speak_to_bytes
    with pytest.raises(ValueError):
        speak_to_bytes("")

def test_tts_rejects_whitespace_only():
    """TTS should raise ValueError for whitespace-only input."""
    from jarvis.tts import speak_to_bytes
    with pytest.raises(ValueError):
        speak_to_bytes("   ")

def test_speak_calls_sounddevice():
    """speak() should call sd.play to play audio."""
    mock_audio = np.ones(100, dtype=np.float32) * 0.1
    mock_stream = MagicMock()
    # Stream is active on first call, then inactive — exits the poll loop
    mock_stream.active = False
    with patch("jarvis.tts._get_pipeline") as mock_get_pipeline, \
         patch("jarvis.tts._load_config", return_value=STUB_CONFIG), \
         patch("jarvis.tts.sd") as mock_sd:
        mock_sd.get_stream.return_value = mock_stream
        mock_pipeline = MagicMock()
        mock_pipeline.return_value = iter([(None, None, mock_audio)])
        mock_get_pipeline.return_value = mock_pipeline
        from jarvis.tts import speak
        speak("Engaging thrusters.")
        mock_sd.play.assert_called_once()


def _stream(spoken_audio):
    from jarvis.tts import SpeechStream
    return SpeechStream(synthesize=lambda s: np.full(10, len(s), dtype=np.float32),
                        play=lambda audio: spoken_audio.append(int(audio[0])))


def test_speech_stream_speaks_sentences_in_order_as_they_arrive():
    played = []
    s = _stream(played)
    for piece in ["Opening Spo", "tify. Playing ", "the album", " now!"]:
        s.feed(piece)
    s.finish()
    assert s.spoken == ["Opening Spotify.", "Playing the album now!"]
    assert played == [len("Opening Spotify."), len("Playing the album now!")]


def test_speech_stream_strips_markdown_and_skips_empty():
    from jarvis.tts import clean_for_speech
    assert clean_for_speech("**Paris** is the `capital`.") == "Paris is the capital."
    assert clean_for_speech("- first item") == "first item"
    played = []
    s = _stream(played)
    s.feed("\n\n## \n")
    s.finish()
    assert s.spoken == [] and played == []


def test_speech_stream_stops_on_interrupt():
    from jarvis import tts
    played = []
    s = _stream(played)
    tts._interrupt.set()          # what stop_speaking() does (Esc, mute, "stop")
    s.feed("One. Two. Three.")
    s.finish()                    # must return, not hang
    assert played == []


def test_speech_stream_first_piece_ends_at_a_pause():
    played = []
    s = _stream(played)
    s.feed("Time slows down near a black hole: to a distant observer, someone falling in")
    s.feed(" would appear to freeze. Strange, right?")
    s.finish()
    assert s.spoken == ["Time slows down near a black hole:",
                        "to a distant observer, someone falling in would appear to freeze.",
                        "Strange, right?"]
    s2 = _stream([])
    s2.feed("Sure, opening it.")        # pause too early (< 14 chars): wait for the sentence
    s2.finish()
    assert s2.spoken == ["Sure, opening it."]
