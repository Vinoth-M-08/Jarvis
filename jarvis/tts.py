from pathlib import Path
import queue
import re
import time
import yaml
import numpy as np
import sounddevice as sd
import threading

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

def _load_config():
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)

_pipeline = None
_voice_tensor = None
_speaking = threading.Event()  # set while audio is playing
_interrupt = threading.Event()  # set to stop streaming

_LOCAL_PTH = Path.home() / "Downloads" / "kokoro-v1_0.pth"

def _get_pipeline():
    """Kokoro runs on the GPU when PyTorch has CUDA (≈5x faster per sentence), else on the CPU."""
    global _pipeline
    if _pipeline is None:
        import torch
        from kokoro import KPipeline, KModel
        repo_id = "hexgrad/Kokoro-82M"
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if _LOCAL_PTH.exists():
            kmodel = KModel(repo_id=repo_id, model=str(_LOCAL_PTH)).to(device).eval()
            _pipeline = KPipeline(lang_code="a", repo_id=repo_id, model=kmodel)
        else:
            _pipeline = KPipeline(lang_code="a", repo_id=repo_id, device=device)
        print(f"[TTS] Kokoro voice on {device.upper()}"
              + ("" if device == "cuda" else " (install CUDA PyTorch for faster replies)"))
    return _pipeline


def _to_numpy(audio) -> np.ndarray:
    """Kokoro gives torch tensors (maybe on the GPU); sounddevice needs numpy on the CPU."""
    if hasattr(audio, "detach"):
        audio = audio.detach().float().cpu().numpy()
    return np.asarray(audio, dtype=np.float32)

def _get_voice():
    """Load voice tensor once and cache it."""
    global _voice_tensor
    if _voice_tensor is None:
        import torch
        cfg = _load_config()
        voice_name = cfg["tts"]["voice"]
        local_voice = _LOCAL_PTH.parent / f"{voice_name}.pt"
        if local_voice.exists():
            _voice_tensor = torch.load(str(local_voice), weights_only=True)
        else:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(
                repo_id="hexgrad/Kokoro-82M",
                filename=f"voices/{voice_name}.pt",
                local_files_only=True,
            )
            _voice_tensor = torch.load(path, weights_only=True)
    return _voice_tensor


def speak_to_bytes(text: str) -> bytes:
    """Convert text to PCM audio bytes using Kokoro TTS."""
    if not text or not text.strip():
        raise ValueError("Text cannot be empty")
    pipeline = _get_pipeline()
    voice = _get_voice()
    cfg = _load_config()
    speed = cfg["tts"]["speed"]
    audio_chunks = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        if audio is not None:
            audio_chunks.append(_to_numpy(audio))
    if not audio_chunks:
        return b""
    combined = np.concatenate(audio_chunks)
    pcm = (combined * 32767).astype(np.int16)
    return pcm.tobytes()


def is_speaking() -> bool:
    """Check if Jarvis is currently speaking."""
    return _speaking.is_set()


_last_spoke_at = 0.0


def _done_speaking() -> None:
    global _last_spoke_at
    _speaking.clear()
    _last_spoke_at = time.time()


def own_voice_audible(tail_s: float = 0.4) -> bool:
    """True while Jarvis is talking and for tail_s after (the speakers' echo) — the mic must not
    treat that as the owner (Jarvis saying "...the Jarvis page" used to trigger its own wake word)."""
    return _speaking.is_set() or time.time() - _last_spoke_at < tail_s


def stop_speaking() -> None:
    """Interrupt current speech playback (within 50 ms). Only flags it: the thread that is playing
    stops its own stream (_wait_or_interrupt). Calling sd.stop() from here, another thread, while
    the player was starting the next sentence crashed Python (access violation, 24 Oct 13:48)."""
    _interrupt.set()
    _playing["audio"] = None
    _done_speaking()


# What's coming out of the speakers right now — barge-in compares the mic against it, so Jarvis's
# own voice (the echo) isn't taken for you talking over it.
_playing: dict = {"audio": None, "start": 0.0, "rate": 24000}


_said: list[tuple[float, str]] = []      # (time, sentence) Jarvis spoke lately


def _note_said(text: str) -> None:
    now = time.time()
    _said.append((now, text))
    while _said and now - _said[0][0] > 60:
        _said.pop(0)


def recently_said(seconds: float = 30) -> str:
    """What Jarvis said in the last `seconds` (to recognise its own voice coming back through the mic)."""
    now = time.time()
    return " ".join(t for at, t in list(_said) if now - at <= seconds)


def _now_playing(audio: np.ndarray, rate: int = 24000) -> None:
    _playing.update(audio=audio, start=time.time(), rate=rate)


def playback_level(window_s: float = 0.3) -> float:
    """Loudest 30 ms of what was played in the last window_s (0 if nothing): the echo the mic may hear."""
    audio = _playing["audio"]
    if audio is None:
        return 0.0
    rate = _playing["rate"]
    pos = int((time.time() - _playing["start"]) * rate)
    lo, hi = max(0, pos - int(window_s * rate)), min(len(audio), pos + int(0.03 * rate))
    if hi <= lo:
        return 0.0
    seg = np.asarray(audio[lo:hi], dtype=np.float32)
    chunk = int(0.03 * rate)
    n = len(seg) // chunk
    if n == 0:
        return float(np.sqrt(np.mean(seg ** 2)))
    return float(np.sqrt(np.mean(seg[:n * chunk].reshape(n, chunk) ** 2, axis=1)).max())


# sounddevice's play()/stop() share one global stream and aren't thread-safe; speech, chimes and
# barge-in run on different threads, so every start, stop and check goes through this lock.
_sd_lock = threading.RLock()


def _start_audio(audio: np.ndarray, rate: int = 24000) -> None:
    with _sd_lock:
        sd.play(audio, samplerate=rate)


def _audio_active() -> bool:
    with _sd_lock:
        try:
            stream = sd.get_stream()
        except RuntimeError:          # nothing played yet
            return False
        return bool(stream and stream.active)


def _wait_or_interrupt() -> bool:
    """Poll until audio finishes or interrupt is set. Returns True if interrupted."""
    while _audio_active():
        if _interrupt.is_set():
            with _sd_lock:
                sd.stop()
            return True
        _interrupt.wait(0.05)  # 50ms poll
    return _interrupt.is_set()


def play_sound(sound: np.ndarray, wait: bool = True) -> None:
    """A chime or beep (not speech)."""
    _start_audio(sound)
    while wait and _audio_active():
        time.sleep(0.02)


def speak(text: str) -> None:
    """Speak text aloud. Can be interrupted via stop_speaking()."""
    _interrupt.clear()
    _note_said(text)
    audio_bytes = speak_to_bytes(text)
    if not audio_bytes:
        return
    audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767
    _speaking.set()
    _now_playing(audio)
    _start_audio(audio)
    _wait_or_interrupt()
    _done_speaking()


def speak_streamed(text: str) -> None:
    """Speak text sentence by sentence; the next sentence is generated while one plays.
    Can be interrupted mid-stream via stop_speaking()."""
    stream = SpeechStream()
    stream.feed(text)
    stream.finish()


# ─── Speaking while the brain is still writing ───

# A sentence ends at . ! ? (then a space) or at a line break.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\s*\n+\s*")
# The first piece may also end at a pause (, ; : —) once it's this long: the voice can start
# as soon as a few words are ready, while the rest of the sentence is still being written.
_CLAUSE_BREAK = re.compile(r"(?<=[,;:])\s+|\s+[—–-]\s+")
_FIRST_PIECE_MIN_CHARS = 14


def clean_for_speech(text: str) -> str:
    """Drop markdown that would otherwise be read out (**bold**, `code`, # headings, list bullets)."""
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)
    text = re.sub(r"[*_#>]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


_SHORT_PHRASE_CACHE: dict[tuple[str, float], np.ndarray] = {}   # "On it.", "Okay." ... play instantly


def _synthesize(text: str) -> np.ndarray | None:
    speed = _load_config()["tts"]["speed"]
    key = (text, speed)
    if key in _SHORT_PHRASE_CACHE:
        return _SHORT_PHRASE_CACHE[key]
    pipeline = _get_pipeline()
    voice = _get_voice()
    chunks = [_to_numpy(audio) for _, _, audio in pipeline(text, voice=voice, speed=speed) if audio is not None]
    audio = np.concatenate(chunks) if chunks else None
    if audio is not None and len(text) <= 24 and len(_SHORT_PHRASE_CACHE) < 64:
        _SHORT_PHRASE_CACHE[key] = audio
    return audio


def prepare_phrases(*phrases: str) -> None:
    """Make the audio for short fixed phrases now, so they play with no delay later."""
    for p in phrases:
        try:
            _synthesize(p)
        except Exception as e:
            print(f"[TTS] Couldn't prepare {p!r}: {e}")


def _play(audio: np.ndarray) -> None:
    _now_playing(audio)
    _start_audio(audio)
    _wait_or_interrupt()


class SpeechStream:
    """Speak text while it is still arriving (e.g. Claude's reply as it is written).

    feed() adds text; every finished sentence is queued at once. One thread turns
    sentences into audio while another plays the previous one, so speech starts after
    the first sentence instead of after the whole reply. finish() speaks the rest and
    waits; stop_speaking() (Esc, mute, "stop") cuts it short.
    """

    def __init__(self, synthesize=_synthesize, play=_play):
        self._synthesize = synthesize
        self._play = play
        self._buffer = ""
        self._lock = threading.Lock()
        self._sentences: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue(maxsize=2)   # make at most 2 sentences ahead
        self.fed_any = False          # anything given to feed() (so the caller won't say it again)
        self.spoken: list[str] = []   # sentences queued for speaking, in order
        self.first_audio_at: float | None = None   # time.time() when the voice started
        _interrupt.clear()
        self._maker = threading.Thread(target=self._make_audio, name="tts-make", daemon=True)
        self._player = threading.Thread(target=self._play_audio, name="tts-play", daemon=True)
        self._maker.start()
        self._player.start()

    def feed(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self.fed_any = True
            self._buffer += text
            parts = _SENTENCE_BREAK.split(self._buffer)
            self._buffer = parts.pop()          # the unfinished tail waits for more text
            for part in parts:
                self._queue(part)
            if not self.spoken:
                self._queue_first_clause()

    def _queue_first_clause(self) -> None:
        """Nothing spoken yet and no full sentence: speak up to the first pause if it's long enough."""
        for m in _CLAUSE_BREAK.finditer(self._buffer):
            if m.start() >= _FIRST_PIECE_MIN_CHARS:
                self._queue(self._buffer[:m.start()])
                self._buffer = self._buffer[m.end():]
                return

    def finish(self) -> None:
        """Speak whatever is left and block until done (or interrupted)."""
        with self._lock:
            self._queue(self._buffer)
            self._buffer = ""
        self._sentences.put(None)
        self._player.join()

    def _queue(self, part: str) -> None:
        sentence = clean_for_speech(part)
        if sentence:
            _note_said(sentence)
            self.spoken.append(sentence)
            self._sentences.put(sentence)

    def _make_audio(self) -> None:
        while True:
            sentence = self._sentences.get()
            if sentence is None or _interrupt.is_set():
                if sentence is not None:
                    self._drain(self._sentences)
                self._audio.put(None)
                return
            try:
                audio = self._synthesize(sentence)
            except Exception as e:
                print(f"[TTS] Couldn't speak a sentence: {e}")
                audio = None
            if audio is not None:
                self._audio.put(audio)

    def _play_audio(self) -> None:
        try:
            while True:
                audio = self._audio.get()
                if audio is None:
                    return
                if _interrupt.is_set():
                    continue                     # keep reading until the maker says it's done
                _speaking.set()
                if self.first_audio_at is None:
                    self.first_audio_at = time.time()
                self._play(audio)
        finally:
            _done_speaking()

    @staticmethod
    def _drain(q: queue.Queue) -> None:
        try:
            while q.get_nowait() is not None:
                pass
        except queue.Empty:
            pass
