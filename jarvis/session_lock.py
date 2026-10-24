"""Is Windows locked? While it is, Jarvis must not listen, speak or act (anyone near the laptop could
otherwise command it, and replies would be read out to the room).

Three signals, any one means locked:
  * the session's own lock state (WTSQuerySessionInformation, WTSSessionInfoEx) — the reliable one;
  * the lock screen app (LockApp.exe) is in front;
  * input is on the secure desktop (lock / sign-in / UAC) — OpenInputDesktop fails or isn't "Default".
The last alone wasn't enough: on 24 Oct it said "unlocked" again within a second while the lock screen
was still up, and Jarvis answered on the lock screen. A LockGuard thread checks twice a second and calls
on_lock / on_unlock when that changes (unlocking needs two checks in a row, so it can't flicker open).
"""
from __future__ import annotations
import os
import sys
import threading
import time
from typing import Callable


def _session_locked() -> bool | None:
    """The session's lock flag; None if Windows won't say."""
    import ctypes
    from ctypes import wintypes
    wtsapi = ctypes.windll.wtsapi32
    buf = ctypes.c_void_p()
    size = wintypes.DWORD()
    # WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, WTSSessionInfoEx (25)
    if not wtsapi.WTSQuerySessionInformationW(None, wintypes.DWORD(0xFFFFFFFF), 25,
                                              ctypes.byref(buf), ctypes.byref(size)):
        return None
    try:
        if size.value < 20:
            return None
        # WTSINFOEXW: DWORD Level, then the 8-byte-aligned WTSINFOEX_LEVEL1_W: ULONG SessionId (8),
        # WTS_CONNECTSTATE_CLASS SessionState (12), LONG SessionFlags (16).
        # WTS_SESSIONSTATE_LOCK = 0, UNLOCK = 1, UNKNOWN = -1.
        flags = ctypes.c_long.from_address(buf.value + 16).value
        return {0: True, 1: False}.get(flags)
    finally:
        wtsapi.WTSFreeMemory(buf)


def _lock_app_in_front() -> bool:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel32.OpenProcess.restype = wintypes.HANDLE
    proc = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not proc:
        return False
    try:
        path = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(len(path))
        if not kernel32.QueryFullProcessImageNameW(proc, 0, path, ctypes.byref(n)):
            return False
        return os.path.basename(path.value).lower() == "lockapp.exe"
    finally:
        kernel32.CloseHandle(proc)


def _secure_desktop() -> bool:
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


def is_locked() -> bool:
    if sys.platform != "win32":
        return False
    checks = (_session_locked, _lock_app_in_front, _secure_desktop)
    for check in checks:
        try:
            if check():
                return True
        except Exception:
            continue
    return False


class LockGuard:
    def __init__(self, on_lock: Callable[[], None] = lambda: None, on_unlock: Callable[[], None] = lambda: None,
                 check: Callable[[], bool] = is_locked, poll_s: float = 0.5, unlock_after: int = 2):
        self._on_lock, self._on_unlock, self._check, self._poll_s = on_lock, on_unlock, check, poll_s
        self._unlock_after = unlock_after
        self._clear_polls = 0
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
        self._clear_polls = 0 if now_locked else self._clear_polls + 1
        if now_locked and not self.locked:
            self._unlocked.clear()
            self._on_lock()
        elif self.locked and self._clear_polls >= self._unlock_after:
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
