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


def test_own_voice_counts_while_speaking_and_just_after(monkeypatch):
    """The mic ignores Jarvis's own voice and the speakers' echo right after it."""
    from jarvis import tts
    clock = [1000.0]
    monkeypatch.setattr(tts.time, "time", lambda: clock[0])
    tts._speaking.set()
    assert tts.own_voice_audible()
    tts._done_speaking()
    clock[0] += 0.2
    assert tts.own_voice_audible(tail_s=0.4)        # echo still in the room
    clock[0] += 0.5
    assert not tts.own_voice_audible(tail_s=0.4)


def test_playback_level_follows_what_is_playing(monkeypatch):
    import numpy as np
    from jarvis import tts
    clock = [100.0]
    monkeypatch.setattr(tts.time, "time", lambda: clock[0])
    audio = np.concatenate([np.full(24000, 0.2, np.float32), np.zeros(24000, np.float32)])
    tts._now_playing(audio)
    clock[0] = 100.5
    assert abs(tts.playback_level() - 0.2) < 1e-3   # loud part playing
    clock[0] = 101.6
    assert tts.playback_level() == 0.0               # silent part
    clock[0] = 103.0
    assert tts.playback_level() == 0.0               # finished
    tts._playing["audio"] = None
