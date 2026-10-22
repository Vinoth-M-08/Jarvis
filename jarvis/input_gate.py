"""Input gate: checks a request before any brain sees it (PLAN.md §4.6, v1).

1. Source check — who started the turn (only voice / typed turns may act; the
   gatekeeper enforces it, the gate records it).
2. Forbidden check — obvious Tier 3 requests are refused here with 0 tokens.
3. Budget check — when today's Claude budget is used up, the request goes to the
   local brain; at the warning level Jarvis tells you once.

This is a cheap first filter, not the safety boundary: every real action is still
checked by the action gatekeeper after the brain.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from jarvis.security.gatekeeper import USER_SOURCES

# (pattern, reason). Kept narrow on purpose: a missed phrase still hits the gatekeeper,
# but a false positive would block a harmless request.
_THE = r"(?:(?:the|my|this|that)\s+)?"
_MACHINE = r"(?:pc|computer|laptop|machine|system)"

FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (rf"\bformat\s+{_THE}(?:[a-z]:|[a-z]\s+drive|drive\s+[a-z]|(?:hard|system|c)\s+(?:drive|disk)|disk|ssd|partition)",
     "formatting a drive"),
    (r"\bdiskpart\b", "disk partitioning"),
    (r"\b(?:delete|wipe|erase|remove)\s+.{0,30}\b(?:system32|windows folder|c:\\?windows\b|program files)",
     "deleting Windows system files"),
    (rf"\b(?:delete|wipe|erase)\s+(?:everything|all\s+(?:my\s+)?files)\s+(?:on|from)\s+{_THE}(?:{_MACHINE}|drive|disk|hard drive)\b",
     "wiping all files"),
    (rf"\b(?:wipe|erase)\s+{_THE}(?:whole\s+|entire\s+)?(?:{_MACHINE}|drive|disk|hard drive|ssd)\b",
     "wiping all files"),
    (r"\b(?:disable|turn off|switch off|uninstall)\s+.{0,15}\b(?:defender|antivirus|anti-virus|firewall|uac|"
     r"user account control|windows security|smartscreen)\b",
     "turning off Windows security protection"),
    (r"\b(?:show|reveal|dump|export|list|read out)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:saved|stored)\s+"
     r"(?:passwords?|credentials?|logins?)\b",
     "reading saved passwords"),
    (r"\b(?:show|reveal|read|tell me)\s+(?:me\s+)?my\s+(?:credit card|card number|otp|2fa code|cvv)",
     "reading payment details or one-time codes"),
    (rf"\b(?:shut ?down|restart|reboot|power off)\s+{_THE}{_MACHINE}\b",
     "shutting down or restarting the computer (not enabled yet)"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in FORBIDDEN_PATTERNS]


@dataclass
class GateDecision:
    refuse: bool = False
    reason: str = ""
    use_local: bool = False      # skip Claude for this request
    notice: str = ""             # one-line note to add to the reply (budget warning)
    can_act: bool = True         # informational: the gatekeeper enforces it

    def refusal_text(self) -> str:
        return f"I can't do that: {self.reason} is blocked for safety. You'd have to do it yourself."


def forbidden_reason(text: str) -> str | None:
    for pattern, reason in _COMPILED:
        if pattern.search(text):
            return reason
    return None


class InputGate:
    def __init__(self, daily_claude_budget: int = 100, warn_at_percent: int = 80):
        self.daily_claude_budget = daily_claude_budget
        self.warn_at_percent = warn_at_percent
        self._warned_day: str | None = None

    def evaluate(self, text: str, source: str, uses_claude: bool,
                 claude_requests_today: int = 0, today: str = "") -> GateDecision:
        decision = GateDecision(can_act=source in USER_SOURCES)

        reason = forbidden_reason(text)
        if reason:
            decision.refuse = True
            decision.reason = reason
            return decision

        if uses_claude and self.daily_claude_budget > 0:
            used = claude_requests_today
            if used >= self.daily_claude_budget:
                decision.use_local = True
                decision.notice = (f"(Today's Claude budget of {self.daily_claude_budget} requests is used up, "
                                   "so the local brain answered.)")
            elif used + 1 >= self.daily_claude_budget * self.warn_at_percent / 100 and self._warned_day != today:
                self._warned_day = today
                decision.notice = f"(Heads up: {used + 1} of {self.daily_claude_budget} Claude requests used today.)"
        return decision
