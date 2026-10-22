"""Decision maker: Jev model when configured, else the rules fallback (PLAN.md §4.8)."""
from __future__ import annotations
import time
from typing import Callable

from jarvis.decision import rules_fallback
from jarvis.decision.jev_client import JevClient
from jarvis.decision.questions import Decision


class DecisionMaker:
    def __init__(self, provider: str = "rules", jev: JevClient | None = None,
                 local_for_simple: bool = False, claude_complexity: int = 5,
                 unclear_threshold: float = 0.7, recent_turn_s: float = 300,
                 clock: Callable[[], float] = time.monotonic):
        self.provider = provider
        self.jev = jev or JevClient()
        self.local_for_simple = local_for_simple
        self.claude_complexity = claude_complexity
        self.unclear_threshold = unclear_threshold
        self.recent_turn_s = recent_turn_s
        self._clock = clock
        self._last_turn = None

    def decide(self, text: str) -> Decision:
        now = self._clock()
        recent = self._last_turn is not None and now - self._last_turn < self.recent_turn_s
        self._last_turn = now
        if self.provider == "jev" and self.jev.available():
            try:
                return self.jev.decide(text)
            except Exception as e:
                print(f"[Decision] Jev failed ({e}), using rules")
        return rules_fallback.decide(text, recent_turn=recent, local_for_simple=self.local_for_simple,
                                     claude_complexity=self.claude_complexity,
                                     unclear_threshold=self.unclear_threshold)
