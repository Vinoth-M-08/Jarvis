import os
import sys
from pathlib import Path
import yaml
import numpy as np

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _add_nvidia_dll_dirs() -> None:
    """Make pip-installed cuBLAS/cuDNN DLLs (nvidia-*-cu12) loadable by CTranslate2 on Windows."""
    if sys.platform != "win32":
        return
    for site in sys.path:
        nvidia = Path(site) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


_add_nvidia_dll_dirs()

def _load_config():
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)

_model = None
_model_device = None  # track what device the model is on

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore

def _get_model(force_cpu=False):
    global _model, _model_device
    if _model is not None and not force_cpu:
        return _model
    cfg = _load_config()
    stt = cfg["stt"]
    device = "cpu" if force_cpu else stt["device"]
    compute_type = "int8" if force_cpu else stt["compute_type"]
    try:
        _model = WhisperModel(stt["model"], device=device, compute_type=compute_type)
        _model_device = device
        print(f"[STT] Loaded {stt['model']} on {device}")
    except Exception as e:
        if device != "cpu":
            print(f"[STT] {device} failed to load ({e}), falling back to CPU")
            _model = WhisperModel(stt["model"], device="cpu", compute_type="int8")
            _model_device = "cpu"
            print(f"[STT] Loaded {stt['model']} on cpu")
        else:
            raise
    return _model

def transcribe_audio(audio: np.ndarray) -> str:
    """Transcribe a float32 numpy audio array (16kHz mono) to text."""
    try:
        model = _get_model()
        segments, _ = model.transcribe(audio, beam_size=5, language="en")
        return " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as e:
        # If CUDA worked for loading but fails during inference, retry on CPU
        if _model_device != "cpu":
            print(f"[STT] {_model_device} transcription failed ({e}), reloading on CPU")
            try:
                model = _get_model(force_cpu=True)
                segments, _ = model.transcribe(audio, beam_size=5, language="en")
                return " ".join(seg.text.strip() for seg in segments).strip()
            except Exception as e2:
                print(f"[STT] CPU transcription also failed: {e2}")
                return ""
        print(f"[STT] Transcription failed: {e}")
        return ""

# Global abort event — set by main.py so recording can be interrupted
_abort_event = None

def set_abort_event(event) -> None:
    """Register the global abort event so recording checks it."""
    global _abort_event
    _abort_event = event

SPEECH_RMS = 0.015  # a 300 ms chunk this loud counts as speech


def has_speech(audio: np.ndarray, sample_rate: int = 16000) -> bool:
    """True if any 300 ms chunk is speech-loud. Whisper turns silence into words
    ("You", "Thank you"), so voice input with no speech must not be transcribed."""
    chunk = int(sample_rate * 0.3)
    if audio.size == 0:
        return False
    usable = audio[: max(chunk, audio.size // chunk * chunk)]
    if usable.size < chunk:
        return float(np.sqrt(np.mean(usable ** 2))) >= SPEECH_RMS
    rms = np.sqrt(np.mean(usable.reshape(-1, chunk) ** 2, axis=1))
    return bool(rms.max() >= SPEECH_RMS)


# ─── End-of-speech detection with Silero VAD (bundled with openWakeWord) ───

VAD_FRAME = 480            # 30 ms at 16 kHz
_vad = None


def _vad_prob_fn():
    """frame (float32, -1..1) -> speech probability. Falls back to loudness if the model won't load."""
    global _vad
    if _vad is None:
        try:
            from openwakeword.vad import VAD
            _vad = VAD()
        except Exception as e:
            print(f"[STT] Silero VAD unavailable ({e}); using loudness to detect speech.")
            _vad = False
    if _vad is False:
        return lambda frame: 1.0 if float(np.sqrt(np.mean(frame ** 2))) >= SPEECH_RMS else 0.0
    _vad.reset_states()
    return lambda frame: float(_vad.predict(frame * 32767, frame_size=VAD_FRAME))


def separate_vad_prob():
    """A speech-probability function with its own Silero state, for a second listener running alongside
    _listen (barge-in). Falls back to loudness like _vad_prob_fn."""
    try:
        from openwakeword.vad import VAD
        vad = VAD()
        return lambda frame: float(vad.predict(frame * 32767, frame_size=VAD_FRAME))
    except Exception:
        return lambda frame: 1.0 if float(np.sqrt(np.mean(frame ** 2))) >= SPEECH_RMS else 0.0


def collect_utterance(frames, speech_prob, frame_s: float = VAD_FRAME / 16000,
                      end_silence_s: float = 0.7, start_timeout_s: float = 8.0, max_seconds: float = 30,
                      threshold: float = 0.5, pre_roll_s: float = 0.3, should_stop=lambda: False,
                      stats: dict | None = None) -> np.ndarray:
    """Read frames until the speaker has been quiet for end_silence_s after speaking.

    Returns the utterance (with pre_roll_s of lead-in), or an empty array if nobody started
    speaking within start_timeout_s. Speech starts after two speech frames in a row (60 ms),
    so a click or a cough edge doesn't count. If given, `stats` gets why it stopped and the
    loudest level / highest speech score seen (for "didn't catch anything" diagnostics).
    """
    keep_before = max(1, round(pre_roll_s / frame_s))
    end_frames = max(1, round(end_silence_s / frame_s))
    frames_seen = 0
    recent: list[np.ndarray] = []      # lead-in before speech starts
    utterance: list[np.ndarray] = []
    speech_run = quiet_run = 0
    started = False
    reason = "ended"
    max_p = max_rms = 0.0
    for frame in frames:
        if should_stop():
            reason = "stopped"
            break
        frames_seen += 1
        p = speech_prob(frame)
        if stats is not None:
            max_p = max(max_p, p)
            max_rms = max(max_rms, float(np.sqrt(np.mean(frame ** 2))))
        if not started:
            recent.append(frame)
            recent = recent[-keep_before:]
            speech_run = speech_run + 1 if p >= threshold else 0
            if speech_run >= 2:
                started = True
                utterance = list(recent)
            elif frames_seen * frame_s >= start_timeout_s:
                reason = "no speech started"
                break
            continue
        utterance.append(frame)
        quiet_run = quiet_run + 1 if p < threshold * 0.7 else 0
        if quiet_run >= end_frames:
            break
        if len(utterance) * frame_s >= max_seconds:
            reason = "too long"
            break
    if stats is not None:
        stats.update(reason=reason, max_speech_score=round(max_p, 2), max_level=round(max_rms, 3),
                     seconds=round(frames_seen * frame_s, 1))
    if not started:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(utterance).astype(np.float32)


def record_utterance(end_silence_s: float = 0.7, start_timeout_s: float = 8.0, max_seconds: float = 30,
                     sample_rate: int = 16000, stats: dict | None = None, ignore=None) -> np.ndarray:
    """Record one spoken request from the mic; stops ~end_silence_s after the speaker stops.
    Empty array if nobody spoke within start_timeout_s (or Esc was pressed).
    While ignore() is true (Jarvis itself is talking) the mic counts as silence."""
    import sounddevice as sd

    prob = _vad_prob_fn()
    stopped = lambda: bool(_abort_event and _abort_event.is_set())
    silence = np.zeros(VAD_FRAME, dtype=np.float32)
    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=VAD_FRAME) as stream:
        def frames():
            while True:
                data, _ = stream.read(VAD_FRAME)
                yield silence if ignore is not None and ignore() else data.flatten()
        audio = collect_utterance(frames(), prob, frame_s=VAD_FRAME / sample_rate,
                                  end_silence_s=end_silence_s, start_timeout_s=start_timeout_s,
                                  max_seconds=max_seconds, should_stop=stopped, stats=stats)
    if stopped():
        print("[STT] Recording aborted.")
    return audio


def preload() -> None:
    """Load Whisper and the VAD at start-up and run one tiny transcription, so the
    first spoken request doesn't wait for model loading or GPU warm-up."""
    _vad_prob_fn()
    model = _get_model()
    segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="en")
    list(segments)


def record_until_silence(
    sample_rate: int = 16000,
    silence_threshold: float = 0.012,
    max_seconds: int = 30,
) -> np.ndarray:
    """Record microphone audio until silence is detected. Returns float32 array.
    Waits for speech to start before counting silence.
    Can be interrupted via the abort event (Esc key)."""
    import sounddevice as sd

    chunk = int(sample_rate * 0.3)  # 300ms chunks
    recording = []
    silent_chunks = 0
    silent_chunks_needed = 7  # ~2.1s of silence to stop
    heard_speech = False
    speech_threshold = SPEECH_RMS  # louder than silence — confirms user is talking

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
        while True:
            # Check abort every chunk (~300ms)
            if _abort_event and _abort_event.is_set():
                print("[STT] Recording aborted.")
                break

            data, _ = stream.read(chunk)
            flat = data.flatten()
            recording.append(flat)
            rms = float(np.sqrt(np.mean(flat ** 2)))

            if rms >= speech_threshold:
                if not heard_speech:
                    print(f"[STT] Speech detected (rms={rms:.4f})")
                heard_speech = True
                silent_chunks = 0
            elif heard_speech and rms < silence_threshold:
                silent_chunks += 1

            if heard_speech and silent_chunks >= silent_chunks_needed:
                print("[STT] Silence detected, stopping recording.")
                break
            if len(recording) * chunk >= max_seconds * sample_rate:
                print(f"[STT] Max recording time reached ({max_seconds}s)")
                break

    if not recording:
        return np.zeros(chunk, dtype=np.float32)
    return np.concatenate(recording)
