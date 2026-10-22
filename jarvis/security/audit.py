"""Audit log: every tool call, its tier, the decision and a result preview (PLAN.md §5)."""
from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    source TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT,
    tier INTEGER,
    decision TEXT NOT NULL,
    reason TEXT,
    result TEXT
)
"""


class AuditLog:
    def __init__(self, db_path: str):
        path = Path(db_path)
        if not path.is_absolute():
            path = _REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record(self, turn_id: str, source: str, tool: str, args: dict,
               tier: int | None, decision: str, reason: str = "", result: str | None = None) -> None:
        args_json = json.dumps(args, default=str)[:500]
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit (ts, turn_id, source, tool, args, tier, decision, reason, result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), turn_id, source, tool, args_json,
                 tier, decision, reason, (result or "")[:200]),
            )
            self._conn.commit()

    def for_turn(self, turn_id: str) -> list[dict]:
        """Every logged call of one request, in order (for its task record)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, tool, args, tier, decision, reason, result FROM audit "
                "WHERE turn_id = ? ORDER BY id", (turn_id,),
            ).fetchall()
        out = []
        for ts, tool, args, tier, decision, reason, result in rows:
            try:
                parsed = json.loads(args) if args else {}
            except ValueError:
                parsed = {}   # args are cut at 500 chars, so long ones may not parse
            out.append({"ts": ts, "tool": tool, "args": parsed if isinstance(parsed, dict) else {},
                        "tier": tier, "decision": decision, "reason": reason or "", "result": result or ""})
        return out

    def rows(self, limit: int = 100) -> list[tuple]:
        with self._lock:
            return self._conn.execute(
                "SELECT ts, turn_id, source, tool, tier, decision, reason FROM audit "
                "ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
