"""TypeSafe Jev model client — slot for when waitlist access arrives (PLAN.md §4.8).

The Jev API isn't public yet, so its request/response shape is NOT written here
from guesswork. When you have access, implement `JevClient.decide()` from
TypeSafe's API docs:
  - send each of decision.questions.QUESTIONS as a typed question
    (choice -> option id, score -> 2..10, noul -> probability) with the command
    text only (never memory, files or history — PLAN.md §5.5),
  - honour `timeout_ms`, log the outbound payload, and return a Decision.
Until then Jarvis uses the rules fallback; nothing is sent anywhere.
"""
from __future__ import annotations

from jarvis.decision.questions import Decision


class JevNotConfigured(RuntimeError):
    pass


class JevClient:
    def __init__(self, api_key: str | None = None, model: str = "jev-latest", timeout_ms: int = 800):
        self.api_key = api_key
        self.model = model
        self.timeout_ms = timeout_ms

    def available(self) -> bool:
        return False  # flip once decide() is implemented against the real API

    def decide(self, text: str) -> Decision:
        raise JevNotConfigured("Jev client not implemented yet — waiting for TypeSafe API access/docs.")
