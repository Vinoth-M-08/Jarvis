# tests/test_console_log.py
import io
import sys

from jarvis import console_log


def test_prints_go_to_console_and_timestamped_log(tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    log = console_log.start(tmp_path)
    print("[Timing] You stopped talking -> Jarvis started speaking: 2.4s")
    print("part one ", end="")
    print("same line")
    sys.stdout._file.close()
    assert "Jarvis started speaking: 2.4s" in console.getvalue()     # console unchanged
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[-2].endswith("[Timing] You stopped talking -> Jarvis started speaking: 2.4s")
    assert lines[-2][:2].isdigit() and lines[-2][2] == ":"           # HH:MM:SS prefix
    assert lines[-1].endswith("part one same line") and lines[-1].count(":") == 2


def test_big_log_is_rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    (tmp_path / "jarvis.log").write_text("x" * (console_log.MAX_BYTES + 1))
    console_log.start(tmp_path)
    sys.stdout._file.close()
    assert (tmp_path / "jarvis.1.log").stat().st_size > console_log.MAX_BYTES
    assert (tmp_path / "jarvis.log").stat().st_size < 200
