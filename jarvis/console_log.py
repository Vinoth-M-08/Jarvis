"""Copy everything Jarvis prints to data/logs/jarvis.log, so the console output (timings,
"didn't catch anything" reasons, errors) survives a closed window. Times are added per line."""
from __future__ import annotations
import sys
import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024   # then jarvis.log -> jarvis.1.log (one old file kept)


class _Tee:
    def __init__(self, stream, file, lock: threading.Lock):
        self._stream, self._file, self._lock = stream, file, lock
        self._at_line_start = True

    def write(self, text: str) -> int:
        try:
            self._stream.write(text)
        except Exception:
            pass
        with self._lock:
            out = []
            for piece in text.splitlines(keepends=True):
                if self._at_line_start:
                    out.append(datetime.now().strftime("%H:%M:%S "))
                out.append(piece)
                self._at_line_start = piece.endswith("\n")
            try:
                self._file.write("".join(out))
                self._file.flush()
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):          # isatty, encoding, fileno ... from the real console
        return getattr(self._stream, name)


def start(log_dir: str | Path = "data/logs") -> Path:
    """Start copying stdout and stderr to log_dir/jarvis.log. Returns the log path."""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log = path / "jarvis.log"
    if log.exists() and log.stat().st_size > MAX_BYTES:
        log.replace(path / "jarvis.1.log")
    f = open(log, "a", encoding="utf-8", errors="replace")
    f.write(f"\n===== Jarvis started {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    f.flush()
    lock = threading.Lock()
    sys.stdout = _Tee(sys.stdout, f, lock)
    sys.stderr = _Tee(sys.stderr, f, lock)
    return log
