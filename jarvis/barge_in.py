"""Talking over Jarvis: while it answers, the mic stays open; when you speak it stops and listens.

The hard part is that the mic also hears Jarvis itself (the speakers' echo). Two checks:
  1. EchoGate learns how loud that echo is compared with what's being played (the ratio mic /
     playback, its median over recent frames) and flags a frame only when it's speech AND clearly
     louder than the echo should be. With headphones the echo is ~0, so any speech is flagged.
  2. A flag is only a suspicion. Jarvis keeps talking while ~1 s of it is transcribed; only if
     confirm(words) says they're yours (not its own words coming back) does it stop. On 24 Oct
     every flag was its own voice and it cut its replies off; now those are ignored.

BargeIn runs one listener per reply: start() when the reply starts, finish() when it's over.
Confirmed: stop speaking, record to the end of what you say, transcribe it and hand it to on_heard
(which makes it the next request and cancels the old one).
"""
from __future__ import annotations
import threading
from collections import deque
from typing import Callable, Iterable

import numpy as np

FRAME = 480                 # 30 ms at 16 kHz
FRAME_S = FRAME / 16000


class EchoGate:
    def __init__(self, margin: float = 3.0, floor: float = 0.02, prob: float = 0.7, need: int = 12, of: int = 16,
                 audible: float = 0.02, min_samples: int = 30):
        self.margin, self.floor, self.prob = margin, floor, prob
        self.need, self.audible, self.min_samples = need, audible, min_samples
        self._ratios: deque[float] = deque(maxlen=400)       # ~12 s of Jarvis talking
        self._recent: deque[bool] = deque(maxlen=of)
        self.k: float | None = None                          # echo loudness per unit of playback

    def update(self, mic_rms: float, ref: float, p: float) -> bool:
        """One 30 ms frame -> True once you've been talking (need of the last `of` frames)."""
        if ref >= self.audible:
            self._ratios.append(mic_rms / ref)
            if len(self._ratios) >= self.min_samples:
                self.k = float(np.median(self._ratios))
        if ref < self.audible:
            echo = 0.0
        elif self.k is None:
            echo = float("inf")                              # not learned yet: don't guess
        else:
            echo = self.k * ref
        self._recent.append(p >= self.prob and mic_rms >= max(self.floor, self.margin * echo))
        return sum(self._recent) >= self.need

    def reset(self) -> None:
        self._recent.clear()


def _mic_frames(stop: threading.Event) -> Iterable[np.ndarray]:
    import sounddevice as sd
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=FRAME) as stream:
        while True:
            data, _ = stream.read(FRAME)
            yield data.flatten()


class BargeIn:
    def __init__(self, on_interrupt: Callable[[], None], on_heard: Callable[[str], None],
                 transcribe: Callable[[np.ndarray], str], reference: Callable[[], float],
                 vad_factory: Callable[[], Callable[[np.ndarray], float]],
                 confirm: Callable[[str], bool] = lambda text: bool(text.strip()),
                 blocked: Callable[[], bool] = lambda: False, gate: EchoGate | None = None,
                 frames: Callable[[threading.Event], Iterable[np.ndarray]] = _mic_frames,
                 end_silence_s: float = 0.7, max_seconds: float = 20, pre_roll_s: float = 0.45,
                 probe_s: float = 1.0):
        self._on_interrupt, self._on_heard, self._transcribe = on_interrupt, on_heard, transcribe
        self._reference, self._vad_factory, self._blocked = reference, vad_factory, blocked
        self._confirm = confirm
        self.gate = gate or EchoGate()        # kept across replies: the echo ratio is the room's, not the reply's
        self._frames = frames
        self._end_frames = max(1, round(end_silence_s / FRAME_S))
        self._max_frames = round(max_seconds / FRAME_S)
        self._pre_frames = max(1, round(pre_roll_s / FRAME_S))
        self._probe_frames = max(1, round(probe_s / FRAME_S))
        self._stop = threading.Event()
        self._paused = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._vad = None

    # ─── control (any thread) ───
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.gate.reset()
        self._thread = threading.Thread(target=self._run, name="barge-in", daemon=True)
        self._thread.start()

    def finish(self, timeout: float = 30) -> None:
        """The reply is over: stop watching — but let you finish a sentence you've started."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def pause(self) -> None:
        """Jarvis is recording an answer itself (a yes/no question): that's not an interruption."""
        with self._lock:
            self._paused += 1

    def resume(self) -> None:
        with self._lock:
            self._paused = max(0, self._paused - 1)

    # ─── listener ───
    def _run(self) -> None:
        try:
            audio = self._capture()
        except Exception as e:
            print(f"[Barge-in] Mic unavailable ({e}).")
            return
        if audio is None:
            return
        try:
            text = self._transcribe(audio).strip()
        except Exception as e:
            print(f"[Barge-in] Couldn't transcribe ({e}).")
            return
        if text:
            self._on_heard(text)

    def _is_you(self, frames: list[np.ndarray]) -> bool:
        try:
            words = self._transcribe(np.concatenate(frames).astype(np.float32)).strip()
        except Exception as e:
            print(f"[Barge-in] Couldn't transcribe ({e}).")
            return False
        return bool(words) and self._confirm(words)

    def _capture(self) -> np.ndarray | None:
        """watching -> (gate flags a voice) probing: ~1 s recorded while Jarvis keeps talking ->
        (the words are yours) capturing: Jarvis stops, record to the end of your sentence."""
        if self._vad is None:
            self._vad = self._vad_factory()
        prob = self._vad
        pre: deque[np.ndarray] = deque(maxlen=self._pre_frames)
        utterance: list[np.ndarray] = []
        mode = "watching"
        quiet = 0
        for frame in self._frames(self._stop):
            if mode == "watching" and self._stop.is_set():
                return None
            if self._blocked():                       # PC locked / muted: drop anything half-heard
                if mode == "capturing":
                    return None
                utterance, mode = [], "watching"
                self._reset(pre)
                continue
            if self._paused and mode != "capturing":
                self._reset(pre)
                mode = "watching"
                continue
            p = prob(frame)
            if mode == "watching":
                pre.append(frame)
                rms = float(np.sqrt(np.mean(frame ** 2)))
                if self.gate.update(rms, self._reference(), p):
                    mode, utterance, quiet = "probing", list(pre), 0
                continue
            utterance.append(frame)
            quiet = quiet + 1 if p < 0.35 else 0
            if mode == "probing":
                if len(utterance) - self._pre_frames >= self._probe_frames or quiet >= self._end_frames:
                    if self._is_you(utterance):
                        print("[Barge-in] You spoke — stopping to listen.")
                        self._on_interrupt()
                        mode = "capturing"
                        if quiet >= self._end_frames:
                            break                     # a short sentence, already finished
                    else:
                        utterance, mode = [], "watching"
                        self._reset(pre)
                        if self._stop.is_set():
                            return None
                continue
            if quiet >= self._end_frames or len(utterance) >= self._max_frames:
                break
        if mode != "capturing":
            return None
        return np.concatenate(utterance).astype(np.float32)

    def _reset(self, pre: deque) -> None:
        pre.clear()
        self.gate.reset()
