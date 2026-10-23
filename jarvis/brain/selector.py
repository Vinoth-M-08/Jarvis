"""Brain selector: Claude or the local backup for each request (PLAN.md §4, Phase 2).

- Claude is the main brain. After Claude fails, it is skipped for a back-off period
  (longer for a usage limit) and the local brain answers, with one spoken notice.
- The local backup model is loaded only while it's needed: before loading, free
  VRAM is checked (gaming etc.), and the model is unloaded once Claude is back.
"""
from __future__ import annotations
import re
import time
from typing import Callable

_LIMIT = re.compile(r"rate.?limit|usage limit|limit reached|limit exceeded|\b429\b|overloaded|quota", re.IGNORECASE)
_AUTH = re.compile(r"log ?in|logged in|auth|credential|api key|401|403", re.IGNORECASE)


def classify_failure(error: Exception | str) -> str:
    """'limit', 'auth' or 'unavailable'."""
    text = str(error)
    if _LIMIT.search(text):
        return "limit"
    if _AUTH.search(text):
        return "auth"
    return "unavailable"


_NOTICES = {
    "limit": "Claude's usage limit is reached, so I'll use the local brain for a while.",
    "auth": "Claude isn't logged in. Run claude and log in. Until then I'll use the local brain.",
    "unavailable": "Claude isn't reachable right now, so I'll use the local brain.",
}


class BrainSelector:
    def __init__(self, limit_backoff_s: float = 900, error_backoff_s: float = 120,
                 clock: Callable[[], float] = time.monotonic):
        self.limit_backoff_s = limit_backoff_s
        self.error_backoff_s = error_backoff_s
        self._clock = clock
        self._down_until = 0.0
        self.state = "ok"          # ok | limit | auth | unavailable
        self.limit_hit_at: float | None = None

    def choose(self, uses_claude: bool, budget_used_up: bool = False) -> str:
        if not uses_claude or budget_used_up:
            return "local"
        if self._clock() < self._down_until:
            return "local"
        return "claude"

    def claude_failed(self, error: Exception | str) -> str | None:
        """Record a failure. Returns a one-time notice when the state changes, else None."""
        kind = classify_failure(error)
        self._down_until = self._clock() + (self.limit_backoff_s if kind == "limit" else self.error_backoff_s)
        if kind == "limit":
            self.limit_hit_at = time.time()
        changed = kind != self.state
        self.state = kind
        return _NOTICES[kind] if changed else None

    def claude_ok(self) -> bool:
        """Record a success. True when Claude just came back (so the local model can be unloaded)."""
        came_back = self.state != "ok"
        self.state = "ok"
        self._down_until = 0.0
        return came_back

    def snapshot(self) -> dict:
        return {"state": self.state,
                "retry_in_s": max(0, int(self._down_until - self._clock())),
                "limit_hit_at": self.limit_hit_at}


class LocalBackup:
    """Tracks the local Ollama model used as backup: VRAM check before loading, unload afterwards."""

    def __init__(self, model: str, keep_alive: str = "5m", min_free_vram_gb: float = 4.0,
                 free_vram_gb: Callable[[], float | None] | None = None,
                 unload_fn: Callable[[str], None] | None = None,
                 is_resident: Callable[[str], bool] | None = None):
        self.model = model
        self.keep_alive = keep_alive
        self.min_free_vram_gb = min_free_vram_gb
        self._free_vram_gb = free_vram_gb or (lambda: None)
        self._unload_fn = unload_fn or _ollama_unload
        self._is_resident = is_resident or _ollama_has_loaded
        self.loaded = False

    def ready(self) -> tuple[bool, str]:
        # Already in GPU memory (loaded earlier, maybe by another program): its own memory
        # must not count against it.
        if self.loaded or self._is_resident(self.model):
            self.loaded = True
            return True, ""
        free = self._free_vram_gb()
        if free is not None and free < self.min_free_vram_gb:
            return False, f"the GPU is busy ({free:.1f} GB free, the local brain needs {self.min_free_vram_gb:.0f})"
        return True, ""

    def mark_used(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        if not self.loaded:
            return
        try:
            self._unload_fn(self.model)
        except Exception as e:
            print(f"[Backup] Could not unload {self.model}: {e}")
        self.loaded = False


def _ollama_has_loaded(model: str) -> bool:
    try:
        import ollama
        return any(m.model == model or m.name == model for m in ollama.ps().models)
    except Exception:
        return False


def _ollama_unload(model: str) -> None:
    import ollama
    ollama.generate(model=model, prompt="", keep_alive=0)
