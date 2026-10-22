"""Notification batching (PLAN.md Phase 2).

- urgent(): spoken right away (timer done, something needs you).
- routine(): queued and spoken together at the next pause, as one sentence,
  so background "done" notices don't interrupt you mid-conversation.
"""
from __future__ import annotations
import threading
import time
from typing import Callable


class Notifier:
    def __init__(self, speak: Callable[[str], None], broadcast: Callable[[dict], None],
                 is_busy: Callable[[], bool], poll_s: float = 5.0):
        self._speak = speak
        self._broadcast = broadcast
        self._is_busy = is_busy
        self._poll_s = poll_s
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def urgent(self, text: str) -> None:
        self._broadcast({"type": "notice", "level": "urgent", "text": text})
        self._speak(text)

    def routine(self, text: str) -> None:
        self._broadcast({"type": "notice", "level": "routine", "text": text})
        with self._lock:
            self._queue.append(text)

    def flush(self) -> str | None:
        """Speak everything queued as one sentence. Returns what was spoken."""
        with self._lock:
            items, self._queue = self._queue, []
        if not items:
            return None
        text = items[0] if len(items) == 1 else "Updates: " + " ".join(items)
        self._speak(text)
        return text

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def start(self) -> None:
        """Background flush at the next pause (no request running, nothing being spoken)."""
        if self._thread is not None:
            return

        def loop():
            while True:
                time.sleep(self._poll_s)
                if self.pending() and not self._is_busy():
                    self.flush()
        self._thread = threading.Thread(target=loop, name="notifier", daemon=True)
        self._thread.start()
