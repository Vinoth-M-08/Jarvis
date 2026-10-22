"""GPU stats via NVML (nvidia-ml-py), for the HUD gauge and VRAM-aware backup (NVIDIA_UPGRADES.md §4).

Returns None when there is no NVIDIA GPU or the library is missing — callers then skip GPU checks.
"""
from __future__ import annotations
import threading

_lock = threading.Lock()
_handle = None
_failed = False


def _get_handle():
    global _handle, _failed
    with _lock:
        if _handle is None and not _failed:
            try:
                import pynvml
                pynvml.nvmlInit()
                _handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                _failed = True
        return _handle


def gpu_stats() -> dict | None:
    handle = _get_handle()
    if handle is None:
        return None
    try:
        import pynvml
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        return {
            "vram_used_gb": round(mem.used / 1e9, 2),
            "vram_total_gb": round(mem.total / 1e9, 2),
            "vram_free_gb": round(mem.free / 1e9, 2),
            "gpu_util_pct": int(util.gpu),
            "temp_c": int(temp),
        }
    except Exception:
        return None


def free_vram_gb() -> float | None:
    stats = gpu_stats()
    return None if stats is None else stats["vram_free_gb"]
