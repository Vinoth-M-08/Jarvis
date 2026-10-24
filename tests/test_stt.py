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


def _frames(pattern, frame=480):
    """'s' = speech frame, '.' = quiet frame (30 ms each)."""
    return [np.full(frame, 0.2 if c == "s" else 0.0, dtype=np.float32) for c in pattern]


def _prob(frame):
    return 1.0 if frame[0] > 0.1 else 0.0


def test_utterance_ends_after_short_silence():
    from jarvis.stt import collect_utterance
    pattern = "." * 20 + "s" * 40 + "." * 100          # 0.6 s quiet, 1.2 s speech, then quiet
    consumed = []

    def frames():
        for f in _frames(pattern):
            consumed.append(f)
            yield f
    audio = collect_utterance(frames(), _prob, end_silence_s=0.6, pre_roll_s=0.3)
    speech_end = 20 + 40
    assert len(consumed) <= speech_end + 21               # stopped ~0.6 s after speech, not 2 s+
    assert audio.size == (10 + 35 + 20) * 480             # 0.3 s lead-in (ends with the 5 start frames) + speech + 0.6 s tail


def test_utterance_times_out_when_nobody_speaks():
    from jarvis.stt import collect_utterance
    audio = collect_utterance(iter(_frames("." * 400)), _prob, start_timeout_s=1.0)
    assert audio.size == 0


def test_single_click_is_not_speech_and_stop_aborts():
    from jarvis.stt import collect_utterance
    assert collect_utterance(iter(_frames("..s......" * 20)), _prob, start_timeout_s=3).size == 0
    assert collect_utterance(iter(_frames("s" * 50)), _prob, should_stop=lambda: True).size == 0


def test_knocks_and_short_noises_are_not_speech():
    from jarvis.stt import collect_utterance
    stats = {}
    # 120 ms bursts (a knock, a clap) never start a recording
    assert collect_utterance(iter(_frames(("ssss" + "." * 20) * 10)), _prob, start_timeout_s=5).size == 0
    # 210 ms of sound, then quiet: started, but too little speech to be a request
    audio = collect_utterance(iter(_frames("." * 5 + "s" * 7 + "." * 60)), _prob, end_silence_s=0.6, stats=stats)
    assert audio.size == 0 and stats["reason"] == "too short to be speech"
    # a short real word ("yes", 360 ms) still counts
    assert collect_utterance(iter(_frames("." * 5 + "s" * 12 + "." * 60)), _prob, end_silence_s=0.6).size > 0


def _seg(text, no_speech=0.05, logprob=-0.3, ratio=1.4):
    seg = MagicMock()
    seg.text, seg.no_speech_prob, seg.avg_logprob, seg.compression_ratio = text, no_speech, logprob, ratio
    return seg


def test_whisper_noise_guesses_are_dropped():
    from jarvis.stt import _join_speech
    assert _join_speech([_seg(" open notepad")]) == "open notepad"
    assert _join_speech([_seg(" Uh...", no_speech=0.9)]) == ""                       # fan / knock
    assert _join_speech([_seg(" Thank you.", no_speech=0.6, logprob=-1.0)]) == ""   # unsure + likely silence
    assert _join_speech([_seg(" la la la la la la", ratio=3.1)]) == ""              # made-up repetition
    assert _join_speech([_seg(" what's the weather"), _seg(" you", no_speech=0.95)]) == "what's the weather"
