# Jarvis — Personal Voice Agent

A voice-controlled personal agent for my Windows laptop. Say **"Hey Jarvis"**,
ask for something, and it answers out loud or does it — opening apps, typing,
searching the web, working with files.

The goal (see [PLAN.md](PLAN.md)) is an agent that:

- works freely inside approved drives and **asks before going further**,
- puts **every action behind a safety gatekeeper** in code, not in the prompt,
- keeps memory, history and logs **on this laptop**,
- uses Claude (via subscription) as the main brain, with a private local model
  as backup.

![Jarvis architecture](docs/architecture.jpg)

---

## Status

| Phase | What | State |
|---|---|---|
| 0 | Setup, voice loop on a local model | ✅ Done |
| 1 | Claude brain + gatekeeper + non-admin account | Next |
| 2–7 | Router, usage meter, desktop control, browser, memory, extras | Planned |

**Working now:** wake word → speech-to-text on the GPU → local LLM (Ollama,
`qwen3:4b`) with 29 tools → spoken reply (Kokoro), plus the web HUD.

**Known issues**

- The end-of-speech wait is a fixed ~2 s. (`qwen3:4b` thinking is now off —
  `think: false` in `config.yaml` — which should cut most of the old ~8 s reply
  time; set `think: true` to turn reasoning back on.)
- `set_volume` / `get_volume` report success but don't change anything.
- No gatekeeper yet — use only on this machine, and keep an eye on it.

---

## Setup (Windows)

**Needs:** Windows 10/11, an NVIDIA GPU (tested on RTX 5050 8 GB),
[Python 3.12](https://www.python.org/downloads/) (Kokoro doesn't support 3.13+),
[uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com/download).

```bash
git clone https://github.com/Vinoth-M-08/Jarvis.git
cd Jarvis

uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt pip
.venv\Scripts\python.exe -m spacy download en_core_web_sm

ollama pull qwen3:4b
```

Speech models (Whisper `small`, Kokoro, the wake word) download on first run.
CUDA libraries for Whisper come from the `nvidia-cublas-cu12` /
`nvidia-cudnn-cu12` packages — no CUDA toolkit install needed. The very first
GPU transcription takes about 90 s while the GPU compiles; after that it's cached.

## Run

```bash
start.bat
```

Wait for **"Jarvis online"** (≈30 s on first start). The HUD opens at
http://localhost:7860 (localhost only).

| Input | Action |
|---|---|
| **"Hey Jarvis"** → wait for "Yes?" → speak | Give a command |
| **"Hey Jarvis"** while busy, or **"stop"** | Interrupt |
| **Esc** | Stop everything |
| **F2** | Type a command in the console |
| **Insert** | Mute / unmute Jarvis's voice |
| HUD chat box | Type a command |

**Troubleshooting**

| Problem | Check |
|---|---|
| Doesn't react to "Hey Jarvis" | Windows mic not muted: *Settings → System → Sound → Input*. Then lower `wake_word.threshold` in `config.yaml` (e.g. 0.3). |
| No voice | Insert / HUD mute toggled? Windows output device and volume. |
| Brain errors | Ollama running? `ollama list` should show `qwen3:4b`. |

---

## Layout

```
config.yaml          settings (models, voice, paths)
start.bat            launcher
jarvis/
  main.py            pipeline: wake → listen → think → speak
  wake.py stt.py tts.py
  web.py static/     HUD (FastAPI + WebSocket, 127.0.0.1)
  memory.py context.py llm.py
  tools/router.py    tool list + dispatch (risky tools disabled)
tests/               pytest
PLAN.md              full build plan
NVIDIA_UPGRADES.md   GPU extras (Broadcast mic, CUDA checks, VRAM gauge)
```

```bash
.venv\Scripts\python.exe -m pytest -q
```

---

## Credits

Started from [PanPenek/JarvisAi](https://github.com/PanPenek/JarvisAi)
(MIT, commit `9dcf62b`) — the Windows voice pipeline, tools and HUD. The first
commit in this repo is that code unchanged. Other projects that shaped the plan
are listed in [PLAN.md §9a](PLAN.md#9a-inspirations--credits).

Built on [openWakeWord](https://github.com/dscripka/openWakeWord),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
[Ollama](https://ollama.com/), [ChromaDB](https://www.trychroma.com/) and
[FastAPI](https://fastapi.tiangolo.com/).

## License

Private project. The upstream code it builds on is MIT-licensed
(© PanPenek; stated in the upstream README, which ships no LICENSE file).
