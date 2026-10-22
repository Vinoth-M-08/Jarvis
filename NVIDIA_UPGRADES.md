# NVIDIA Upgrades for Jarvis

> Ways to get more out of the **RTX 5050 laptop GPU (8 GB VRAM)** for Jarvis.
> Companion to [PLAN.md](PLAN.md). Ordered by value: do the "Now" items first.

| # | Upgrade | When | Effort | Value |
|---|---|---|---|---|
| 1 | NVIDIA Broadcast (mic noise + echo removal) | **Now** | 15 min | ⭐⭐⭐ |
| 2 | Drivers + CUDA 12.8+ compatibility | **Now** | 10 min | ⭐⭐⭐ |
| 3 | Force NVIDIA GPU for Jarvis processes | **Now** | 5 min | ⭐⭐ |
| 4 | GPU gauge + VRAM-aware backup (NVML) | Phase 2 | ½ day | ⭐⭐ |
| 5 | Kokoro TTS on GPU (cu128 PyTorch) | Phase 5 (optional) | 1 hr | ⭐ |
| 6 | NVIDIA Parakeet speech-to-text | Phase 7 (optional) | 1–2 days | ⭐ |
| — | NIM, G-Assist, TensorRT | Reference only | — | — |

---

## 1. NVIDIA Broadcast ⭐ (Now)

**What:** free NVIDIA app that uses the RTX GPU to remove background noise and room echo from your mic (and optionally speaker output). It creates a virtual device named **"Microphone (NVIDIA Broadcast)"**.

**Why for Jarvis**
- Cleaner audio → **better faster-whisper accuracy**.
- Fan / keyboard / TV noise removed → **fewer false "Hey Jarvis" wakes**.
- Real silence between words → **better end-of-speech detection** (helps the fork's volume threshold now and Silero VAD later).
- Less of Jarvis's own voice leaking back into the mic.
- Cost: small (a few hundred MB VRAM).

**Steps**
1. Install **NVIDIA Broadcast** (from nvidia.com or via the NVIDIA App).
2. **Microphone** tab → source = your real mic → enable **Noise Removal** and **Room Echo Removal**.
3. Optional **Speakers** tab → enable **Noise Removal** only if you hear issues; otherwise leave off.
4. Point Jarvis at the Broadcast mic — either:
   - Windows **Settings → System → Sound → Input** → set **Microphone (NVIDIA Broadcast)** as default (the fork uses the default input device), **or**
   - add an explicit device to `config.yaml` (small change in `stt.py` / `wake.py`):
     ```yaml
     stt:
       input_device: "NVIDIA Broadcast"   # substring match on sounddevice device name
     ```
5. List devices to confirm the name:
   ```powershell
   python -c "import sounddevice as sd; print(sd.query_devices())"
   ```

**Done when:** wake word triggers reliably with a fan/music in the background, and transcripts are cleaner than before.

**Tune after:** wake threshold (`wake_word.threshold`) and the fork's silence thresholds in `stt.py` may need small adjustments, since the background is now quieter.

---

## 2. Drivers + CUDA 12.8+ (Now)

**Why:** the RTX 50 series (Blackwell) needs **CUDA 12.8 or newer** builds. Older builds fail with errors like *"no kernel image is available for execution on the device"*.

**Steps**
1. Install/update with the **NVIDIA App** → prefer the **Studio driver** (more stable for AI workloads than Game Ready).
2. Check driver + CUDA version:
   ```powershell
   nvidia-smi
   ```
   The header should show **CUDA Version: 12.8** or higher.
3. Keep the pip CUDA libraries recent (already in `requirements.txt`):
   ```powershell
   pip install -U nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
   ```
4. Verify faster-whisper sees the GPU:
   ```powershell
   python -c "import ctranslate2; print('CUDA devices:', ctranslate2.get_cuda_device_count())"
   ```
5. Keep **Ollama** updated (current versions support RTX 50); check it uses the GPU:
   ```powershell
   ollama ps
   ```
   Expect `100% GPU` while a model is loaded.

**Done when:** `nvidia-smi` shows CUDA ≥ 12.8, CTranslate2 reports ≥ 1 device, `ollama ps` shows `100% GPU`.

---

## 3. Force the NVIDIA GPU for Jarvis (Now)

Laptops switch between the Intel iGPU and the NVIDIA GPU (Optimus). Make sure Jarvis always gets the RTX 5050.

**Steps**
- **NVIDIA Control Panel → Manage 3D settings → Program settings** → add `python.exe` (the one in Jarvis's `.venv`) and `ollama.exe` → **High-performance NVIDIA processor**.
- Or Windows **Settings → System → Display → Graphics** → add the same apps → **High performance**.
- Use **Best performance** power mode when plugged in; on battery, expect slower responses.

**Done when:** `nvidia-smi` lists `python.exe` / `ollama.exe` under processes while Jarvis is working.

---

## 4. GPU gauge + VRAM-aware backup (Phase 2)

**What:** read GPU stats with NVML (`nvidia-ml-py`) and use them in the HUD and the brain selector.

**Install**
```powershell
pip install nvidia-ml-py
```

**Sketch** (`jarvis/gpu.py`)
```python
import pynvml

pynvml.nvmlInit()
_h = pynvml.nvmlDeviceGetHandleByIndex(0)

def gpu_stats() -> dict:
    mem = pynvml.nvmlDeviceGetMemoryInfo(_h)
    util = pynvml.nvmlDeviceGetUtilizationRates(_h)
    temp = pynvml.nvmlDeviceGetTemperature(_h, pynvml.NVML_TEMPERATURE_GPU)
    return {
        "vram_used_gb": mem.used / 1e9,
        "vram_free_gb": mem.free / 1e9,
        "gpu_util_pct": util.gpu,
        "temp_c": temp,
    }
```

**Use it for**
- **HUD gauge:** VRAM used/free, GPU load, temperature — next to the Claude 5h/7d gauges.
- **VRAM-aware backup** (brain selector, PLAN.md §4.5):
  - Before loading the Ollama backup model, check `vram_free_gb`.
  - `≥ 4 GB free` → load `qwen3:4b-q4_K_M` as normal.
  - `< 4 GB free` (e.g. gaming) → Jarvis says *"GPU is busy — I'll wait for Claude or use router commands only"*, or loads a smaller model if configured.
- **Thermal note:** if `temp_c` stays very high, show a HUD warning (laptop cooling).

**Config**
```yaml
gpu:
  monitor: true
  min_free_vram_gb_for_backup: 4
  small_backup_model: ""        # optional smaller model when VRAM is tight
```

**Done when:** HUD shows live GPU stats; starting a game then hitting the Claude limit makes Jarvis skip loading the backup model and say why.

---

## 5. Kokoro TTS on GPU (Phase 5, optional)

Kokoro runs fine on CPU (the fork's default). Move it to GPU only if speech generation feels slow.

- Needs PyTorch built for **CUDA 12.8 (cu128)** — older builds don't support RTX 50:
  ```powershell
  pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu128
  ```
- Verify:
  ```powershell
  python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
  ```
- Change device in `tts.py` from CPU to `cuda`. Costs roughly 0.5–1 GB VRAM — check the VRAM budget in PLAN.md §3 (fine in normal mode; tighter in backup mode).

---

## 6. NVIDIA Parakeet speech-to-text (Phase 7, optional)

- NVIDIA's **Parakeet** ASR models are very fast and accurate for English.
- Trade-off: heavier setup on Windows (NVIDIA NeMo toolkit dependencies) than faster-whisper.
- **Recommendation:** stay on faster-whisper (`small` → `medium` on GPU). Try Parakeet only if accuracy or latency is still a problem after Broadcast + VAD.
- If tried: keep it behind the same `stt.transcribe()` interface so it's a config switch.

---

## Reference only (not planned)

| Tool | What it is | Why not now |
|---|---|---|
| **NVIDIA NIM** (build.nvidia.com) | Hosted model APIs; the fork already has a NIM provider option | Cloud — conflicts with the privacy goal; Claude + local backup already cover the brain |
| **Project G-Assist** | NVIDIA's on-device assistant for PC/GPU settings, with plugins | Similar idea; useful for inspiration. VRAM requirements on 8 GB cards not confirmed — check before relying on it |
| **TensorRT / TensorRT-LLM** | Inference speed-up libraries | Ollama is already fast enough on this GPU; extra complexity not worth it |
| **RTX Video / Maxine / Audio2Face** | Video and avatar effects | Not relevant to Jarvis |

---

## Checklist

**Now**
- [ ] NVIDIA Broadcast installed; Noise Removal + Room Echo Removal on
- [ ] Jarvis uses **Microphone (NVIDIA Broadcast)**
- [ ] Studio driver installed; `nvidia-smi` shows CUDA ≥ 12.8
- [ ] `ctranslate2.get_cuda_device_count()` ≥ 1
- [ ] `ollama ps` shows `100% GPU`
- [ ] `python.exe` (venv) and `ollama.exe` set to high-performance NVIDIA GPU

**Phase 2**
- [ ] `jarvis/gpu.py` (NVML) + HUD GPU gauge
- [ ] VRAM-aware backup loading in the brain selector

**Optional**
- [ ] Kokoro on GPU with cu128 PyTorch (Phase 5)
- [ ] Parakeet STT trial (Phase 7)
