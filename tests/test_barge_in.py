# tests/test_barge_in.py — talking over Jarvis stops it; its own voice (the echo) doesn't
import numpy as np

from jarvis.barge_in import FRAME, BargeIn, EchoGate


def _feed(gate, frames):
    """frames: (mic_rms, playback_rms, speech_prob) -> True if any frame triggered."""
    return any(gate.update(m, r, p) for m, r, p in frames)


def test_jarvis_own_voice_never_counts():
    gate = EchoGate()
    assert not _feed(gate, [(0.05, 0.1, 0.9)] * 300)      # speakers' echo: speech-like, but just the echo
    assert gate.k is not None and abs(gate.k - 0.5) < 1e-6


def test_you_talking_over_jarvis_counts():
    gate = EchoGate()
    _feed(gate, [(0.05, 0.1, 0.9)] * 60)                  # learns the echo first
    assert _feed(gate, [(0.25, 0.1, 0.9)] * 12)           # you: 5x louder than the echo


def test_slightly_louder_echo_or_noise_does_not_count():
    gate = EchoGate()
    _feed(gate, [(0.05, 0.1, 0.9)] * 60)
    assert not _feed(gate, [(0.08, 0.1, 0.9)] * 40)       # a louder word from Jarvis
    assert not _feed(gate, [(0.30, 0.1, 0.2)] * 40)       # loud, but not speech (a knock, music)


def test_nothing_counts_over_jarvis_before_the_echo_is_learned_but_gaps_do():
    gate = EchoGate()
    assert not _feed(gate, [(0.5, 0.1, 0.95)] * 20)
    gate.reset()
    assert _feed(gate, [(0.05, 0.0, 0.9)] * 16)           # between sentences: any speech is you


def test_headphones_any_speech_counts():
    gate = EchoGate()
    _feed(gate, [(0.001, 0.1, 0.1)] * 60)                 # almost no echo
    assert _feed(gate, [(0.04, 0.1, 0.9)] * 16)


def test_a_short_word_is_not_enough():
    gate = EchoGate()
    assert not _feed(gate, [(0.2, 0.0, 0.9)] * 5 + [(0.0, 0.0, 0.0)] * 20)


# ─── the listener ───

def _frame(level):
    return np.full(FRAME, level, dtype=np.float32)


def _barge(frames, playing, paused_until=-1, blocked=lambda: False, words="open notepad instead",
           confirm=lambda text: "notepad" in text):
    """Runs the listener on fake mic frames. `playing` = what Jarvis's speakers play per frame,
    until it's interrupted (then silence)."""
    events = {"interrupt": 0, "heard": [], "audio": [], "interrupted_at": None}
    n = {"i": 0}

    def reference():
        stopped = events["interrupted_at"] is not None
        return 0.0 if stopped else playing[min(n["i"], len(playing) - 1)]

    def source(stop):
        for i, f in enumerate(frames):
            n["i"] = i
            if i == paused_until:
                b.resume()
            yield f

    def transcribe(audio):
        events["audio"].append(audio)
        return words

    def interrupt():
        events["interrupt"] += 1
        events["interrupted_at"] = n["i"]

    b = BargeIn(on_interrupt=interrupt, on_heard=events["heard"].append, transcribe=transcribe,
                reference=reference, confirm=confirm,
                vad_factory=lambda: (lambda f: 0.9 if abs(float(f[0])) >= 0.01 else 0.0),
                blocked=blocked, frames=source)
    if paused_until >= 0:
        b.pause()
    b._run()
    return events


def test_your_words_stop_jarvis_after_they_are_confirmed():
    frames = [_frame(0.05)] * 60 + [_frame(0.3)] * 60 + [_frame(0.0)] * 40
    ev = _barge(frames, [0.1] * 160)
    assert ev["interrupt"] == 1
    assert ev["heard"] == ["open notepad instead"]
    # it kept talking while it checked (~1 s after you started), not at the first sound
    assert 60 + 12 + 30 <= ev["interrupted_at"] <= 60 + 12 + 40
    probe, full = ev["audio"]
    assert len(probe) < len(full)
    assert 2.0 < len(full) / 16000 < 3.5                  # lead-in + your words + the pause


def test_its_own_voice_is_flagged_but_never_cuts_it_off():
    frames = [_frame(0.05)] * 60 + [_frame(0.3)] * 60 + [_frame(0.05)] * 60
    ev = _barge(frames, [0.1] * 180, words="let's start by asking")   # Jarvis's own words came back
    assert ev["interrupt"] == 0 and ev["heard"] == []
    assert len(ev["audio"]) >= 1                          # it did check


def test_a_short_confirmed_sentence_is_taken_whole():
    frames = [_frame(0.0)] * 10 + [_frame(0.3)] * 18 + [_frame(0.0)] * 40
    ev = _barge(frames, [0.0] * 68, words="stop notepad")
    assert ev["interrupt"] == 1 and ev["heard"] == ["stop notepad"]


def test_listener_ignores_echo_only():
    ev = _barge([_frame(0.05)] * 200, [0.1] * 200)
    assert ev["interrupt"] == 0 and ev["heard"] == [] and ev["audio"] == []


def test_yes_no_answers_while_paused_are_not_interruptions():
    frames = [_frame(0.3)] * 30 + [_frame(0.0)] * 40
    ev = _barge(frames, [0.0] * 70, paused_until=60)
    assert ev["interrupt"] == 0 and ev["heard"] == []


def test_nothing_while_the_pc_is_locked():
    ev = _barge([_frame(0.3)] * 30 + [_frame(0.0)] * 40, [0.0] * 70, blocked=lambda: True)
    assert ev["interrupt"] == 0 and ev["heard"] == [] and ev["audio"] == []
