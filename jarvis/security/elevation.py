"""Temporary full access (PLAN.md §5.4).

"Jarvis, full access for 10 minutes" turns Tier 1 (CONFIRM) actions into Tier 0
until it expires. Tier 2 (STRONG) and Tier 3 (NEVER) never change.
Granting it always needs a click.
"""
from __future__ import annotations
import re
import threading
import time
from typing import Callable

_GRANT = re.compile(r"\bfull access(?: for)? (\d{1,3}) ?(?:min|mins|minute|minutes)\b")
_REVOKE = re.compile(r"\b(end|stop|revoke|cancel|turn off|disable) full access\b|\bfull access off\b")


def parse_command(text: str) -> tuple[str, int] | None:
    """("grant", minutes) or ("revoke", 0) when the text is an elevation command, else None."""
    t = text.lower().strip()
    if _REVOKE.search(t):
        return "revoke", 0
    m = _GRANT.search(t)
    if m:
        return "grant", int(m.group(1))
    return None


class Elevation:
    def __init__(self, max_minutes: int = 15,
                 on_change: Callable[[bool, int], None] | None = None,
                 on_expire: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.max_minutes = max_minutes
        self.on_change = on_change
        self.on_expire = on_expire
        self._clock = clock
        self._until = 0.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def grant(self, minutes: int) -> int:
        """Start (or restart) elevation. Returns the minutes actually granted (capped)."""
        minutes = max(1, min(int(minutes), self.max_minutes))
        with self._lock:
            self._until = self._clock() + minutes * 60
            self._restart_timer(minutes * 60)
        self._notify()
        return minutes

    def revoke(self) -> None:
        with self._lock:
            was_active = self._until > self._clock()
            self._until = 0.0
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if was_active:
            self._notify()

    def active(self) -> bool:
        return self.remaining_s() > 0

    def remaining_s(self) -> int:
        return max(0, int(self._until - self._clock()))

    def _restart_timer(self, seconds: float) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self) -> None:
        with self._lock:
            self._until = 0.0
            self._timer = None
        self._notify()
        if self.on_expire:
            self.on_expire()

    def _notify(self) -> None:
        if self.on_change:
            self.on_change(self.active(), self.remaining_s())
