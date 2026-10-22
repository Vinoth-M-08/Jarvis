"""Answer cache: general questions answered once are spoken from cache later — 0 tokens (PLAN.md §4.6).

Only stored when it is safe to reuse:
- the request is a general question (what / who / why / how / explain / define ...),
- it isn't about you or your PC ("my", "I", "me", "this", files, folders),
- it isn't time-sensitive (weather, prices, news, today, latest ...),
- the brain answered without calling any tool.
Exact match on the normalized question; entries expire after ttl_days.
"""
from __future__ import annotations
import re
import sqlite3
import threading
import time
from pathlib import Path

from jarvis.router_cmds import normalize

_REPO_ROOT = Path(__file__).parent.parent.parent

_QUESTION = re.compile(r"^(?:what|who|why|how|when|where|which|explain|define|describe|tell me about|what's|whats)\b")
_PERSONAL = re.compile(r"\b(?:my|mine|i|i'm|me|we|our|this|that|these|those|here|file|files|folder|screen|window|pc|computer|laptop)\b")
_TIME_SENSITIVE = re.compile(
    r"\b(?:weather|temperature|forecast|price|prices|cost|stock|stocks|share|news|today|tonight|tomorrow|yesterday|"
    r"now|current|currently|latest|recent|this week|this month|this year|score|live|time|date)\b")

_SCHEMA = "CREATE TABLE IF NOT EXISTS answers (question TEXT PRIMARY KEY, answer TEXT NOT NULL, ts REAL NOT NULL)"


def cacheable(question: str) -> bool:
    q = normalize(question)
    return bool(q) and bool(_QUESTION.match(q)) and not _PERSONAL.search(q) and not _TIME_SENSITIVE.search(q)


class AnswerCache:
    def __init__(self, db_path: str = "data/cache.db", ttl_days: float = 7, clock=time.time):
        path = Path(db_path)
        if not path.is_absolute():
            path = _REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_s = ttl_days * 86400
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, question: str) -> str | None:
        if not cacheable(question):
            return None
        with self._lock:
            row = self._conn.execute("SELECT answer, ts FROM answers WHERE question = ?", (normalize(question),)).fetchone()
        if row and self._clock() - row[1] <= self.ttl_s:
            return row[0]
        return None

    def put(self, question: str, answer: str) -> bool:
        if not cacheable(question) or not answer.strip():
            return False
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO answers (question, answer, ts) VALUES (?, ?, ?)",
                               (normalize(question), answer, self._clock()))
            self._conn.commit()
        return True

    def clear(self) -> int:
        with self._lock:
            n = self._conn.execute("DELETE FROM answers").rowcount
            self._conn.commit()
        return n
