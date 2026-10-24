# tests/test_session_lock.py — while Windows is locked, Jarvis doesn't listen, speak or act
import sys
import threading

import pytest

from jarvis.session_lock import LockGuard, is_locked


def test_guard_reports_lock_and_unlock_once_each():
    state, events = [False], []
    g = LockGuard(on_lock=lambda: events.append("lock"), on_unlock=lambda: events.append("unlock"),
                  check=lambda: state[0])
    g.poll()
    assert not g.locked and events == []
    state[0] = True
    g.poll(); g.poll()
    assert g.locked and events == ["lock"]
    state[0] = False
    g.poll()
    assert not g.locked and events == ["lock", "unlock"]


def test_wait_unlocked_blocks_until_unlock():
    state = [True]
    g = LockGuard(check=lambda: state[0])
    g.poll()
    released = threading.Event()
    threading.Thread(target=lambda: (g.wait_unlocked(), released.set()), daemon=True).start()
    assert not released.wait(0.2)              # still locked: the voice loop waits here
    state[0] = False
    g.poll()
    assert released.wait(2)


def test_a_failing_check_changes_nothing():
    g = LockGuard(check=lambda: (_ for _ in ()).throw(OSError("no desktop")))
    g.poll()
    assert not g.locked


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_is_locked_is_false_while_this_test_runs():
    assert is_locked() is False                # someone is at the unlocked desktop running the tests


def test_tools_are_blocked_and_speech_suppressed_while_locked(monkeypatch):
    """The real Jarvis checks: tools refused, nothing spoken, requests refused, no recording."""
    import subprocess
    script = r'''
import re, sys, types
from pathlib import Path
src = Path("jarvis/main.py").read_text(encoding="utf-8")
src = re.sub(r'if __name__ == "__main__":\s*\n\s*main\(\)\s*$', "pass\n", src)
m = types.ModuleType("__main__"); m.__file__ = str(Path("jarvis/main.py").resolve()); sys.modules["__main__"] = m
exec(compile(src, m.__file__, "exec"), m.__dict__)
spoken = []
m.speak = spoken.append
m.lock_guard._check = lambda: True
m.lock_guard.on(lambda: None, lambda: None)
m.lock_guard.poll()
turn = m.gatekeeper.new_turn("voice")
assert m._exec_tool_with_retry(turn, "open_app", {"name": "notepad"}).startswith("BLOCKED: the PC is locked")
m._speak_if_unmuted("secret reply")
assert spoken == [], spoken
assert m._new_speech_stream() is None
assert m._process_request_inner("open notepad", source="voice").startswith("The PC is locked")
assert m._listen(start_timeout_s=1) == ""
print("LOCKED-OK")
'''
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=300)
    assert "LOCKED-OK" in out.stdout, out.stdout[-1500:] + out.stderr[-3000:]
