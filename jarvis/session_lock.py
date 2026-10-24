"""Is Windows locked? While it is, Jarvis must not listen, speak or act (anyone near the laptop could
otherwise command it, and replies would be read out to the room).

When the PC is locked (or a UAC prompt is up) input switches to the secure desktop, which a normal
program can't open — OpenInputDesktop fails, or names a desktop other than "Default".
A LockGuard thread checks twice a second and calls on_lock / on_unlock when that changes.
"""
from __future__ import annotations
import sys
import threading
import time
from typing import Callable


def is_locked() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    desktop = user32.OpenInputDesktop(0, False, 0x0100)          # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True                                               # secure desktop: lock screen / UAC
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        ok = user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed))  # UOI_NAME
        return bool(ok) and name.value.lower() != "default"
    finally:
        user32.CloseDesktop(desktop)


class LockGuard:
    def __init__(self, on_lock: Callable[[], None] = lambda: None, on_unlock: Callable[[], None] = lambda: None,
                 check: Callable[[], bool] = is_locked, poll_s: float = 0.5):
        self._on_lock, self._on_unlock, self._check, self._poll_s = on_lock, on_unlock, check, poll_s
        self._unlocked = threading.Event()
        self._unlocked.set()
        self._thread: threading.Thread | None = None

    def on(self, lock: Callable[[], None], unlock: Callable[[], None]) -> None:
        self._on_lock, self._on_unlock = lock, unlock

    @property
    def locked(self) -> bool:
        return not self._unlocked.is_set()

    def wait_unlocked(self, timeout: float | None = None) -> bool:
        return self._unlocked.wait(timeout)

    def poll(self) -> None:
        """One check (the thread calls this; tests call it directly)."""
        try:
            now_locked = self._check()
        except Exception:
            return
        if now_locked and not self.locked:
            self._unlocked.clear()
            self._on_lock()
        elif not now_locked and self.locked:
            self._unlocked.set()
            self._on_unlock()

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop():
            while True:
                self.poll()
                time.sleep(self._poll_s)
        self._thread = threading.Thread(target=loop, name="lock-guard", daemon=True)
        self._thread.start()


# The one guard Jarvis uses (other modules read `guard.locked`).
guard = LockGuard()
