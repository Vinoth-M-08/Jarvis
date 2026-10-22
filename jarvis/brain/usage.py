"""Claude usage log: one row per Claude request, for the daily budget (PLAN.md §4.3, v1).

The 5-hour / 7-day gauges come in Phase 2; this is the local count they build on.
"""
from __future__ import annotations
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claude_usage (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_input_tokens INTEGER,
    num_turns INTEGER
)
"""


class UsageTracker:
    def __init__(self, db_path: str = "data/usage.db"):
        path = Path(db_path)
        if not path.is_absolute():
            path = _REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record(self, input_tokens: int | None, output_tokens: int | None,
               cache_read_input_tokens: int | None, num_turns: int | None, day: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO claude_usage (ts, day, input_tokens, output_tokens, cache_read_input_tokens, num_turns) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), day or today(),
                 input_tokens, output_tokens, cache_read_input_tokens, num_turns),
            )
            self._conn.commit()

    def requests_on(self, day: str | None = None) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM claude_usage WHERE day = ?", (day or today(),)).fetchone()
        return int(row[0])


def today() -> str:
    """Local calendar day — the budget resets at your midnight."""
    return date.today().isoformat()
