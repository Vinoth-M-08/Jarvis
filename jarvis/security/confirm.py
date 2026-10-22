"""Confirm broker: asks you before a Tier 1/2 action and waits for the answer (PLAN.md §5).

- The question is shown in the HUD (Allow / Deny) and spoken aloud.
- Tier 1 (CONFIRM): a click or a spoken "yes"/"no" answers it.
- Tier 2 (STRONG): only a click counts — TVs and recordings can say "yes".
- No answer within the timeout, or Esc / abort, means deny.
"""
from __future__ import annotations
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable

from jarvis.security.gatekeeper import ConfirmRequest
from jarvis.security.policy import Tier

_NO = re.compile(r"\b(no|nope|deny|denied|don'?t|do not|stop|cancel|negative)\b")
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok(ay)?|allow|approve[d]?|confirm(ed)?|go ahead|do it)\b")


def parse_yes_no(text: str) -> bool | None:
    """True for yes, False for no, None if unclear. "No" wins when both appear."""
    t = text.lower()
    if _NO.search(t):
        return False
    if _YES.search(t):
        return True
    return None


def describe(tool: str, args: dict) -> str:
    """One short line describing the action, for the HUD and the spoken question."""
    if tool == "full_access":
        return f"turn on full access for {args.get('minutes')} minutes"
    if tool == "plan":
        return f"run this plan: {args.get('intent', '')}"
    if "path" in args:
        return f"{tool.replace('_', ' ')} {args['path']}"
    if tool == "press_key":
        return f"press {args.get('keys', '')}"
    if tool in ("type_text", "set_clipboard"):
        text = str(args.get("text", ""))
        return f"{tool.replace('_', ' ')}: \"{text[:60]}{'…' if len(text) > 60 else ''}\""
    if tool in ("open_url", "fetch_page"):
        return f"{tool.replace('_', ' ')} {args.get('url', '')}"
    if tool == "open_app":
        return f"open {args.get('name', '')}"
    if tool == "click_at":
        return f"click at {args.get('x')}, {args.get('y')}"
    return tool.replace("_", " ")


@dataclass
class _Pending:
    request: ConfirmRequest
    done: threading.Event = field(default_factory=threading.Event)
    allowed: bool = False
    via: str = "timeout"
    choice: str = "deny"       # "allow" | "alternative" | "deny"


class ConfirmBroker:
    """Thread-safe: ask() blocks the calling (voice/HUD worker) thread; resolve() comes from the HUD."""

    def __init__(self,
                 broadcast: Callable[[dict], None],
                 speak: Callable[[str], None] | None = None,
                 listen: Callable[[], str] | None = None,
                 abort_event: threading.Event | None = None,
                 timeout_s: float = 30.0):
        self._broadcast = broadcast
        self._speak = speak
        self._listen = listen
        self._abort = abort_event
        self.timeout_s = timeout_s
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def ask(self, req: ConfirmRequest) -> bool:
        return self.ask_choice(req) == "allow"

    def ask_choice(self, req: ConfirmRequest, steps: list[str] | None = None,
                   alternative: str | None = None) -> str:
        """Like ask(), for plan cards: returns "allow", "alternative" (the safer option) or "deny"."""
        rid = uuid.uuid4().hex[:10]
        pending = _Pending(req)
        with self._lock:
            self._pending[rid] = pending
        summary = describe(req.tool, req.args)
        voice_ok = req.tier == Tier.CONFIRM
        self._broadcast({
            "type": "confirm_request", "id": rid, "tool": req.tool, "summary": summary,
            "tier": int(req.tier), "tier_name": req.tier.name, "reason": req.reason,
            "voice_ok": voice_ok, "timeout_s": self.timeout_s,
            "steps": steps or [], "alternative": alternative or "",
        })
        if self._speak:
            how = "Say yes or no, or click on the screen." if voice_ok else "Please click Allow or Deny on the screen."
            self._speak(f"I need your OK to {summary}. {how}")
        if voice_ok and self._listen:
            threading.Thread(target=self._listen_for_answer, args=(rid,), daemon=True).start()

        waited = 0.0
        while not pending.done.wait(0.25):
            waited += 0.25
            if waited >= self.timeout_s or (self._abort is not None and self._abort.is_set()):
                break
        with self._lock:
            self._pending.pop(rid, None)
        self._broadcast({"type": "confirm_resolved", "id": rid,
                         "allowed": pending.allowed, "via": pending.via})
        return pending.choice if pending.done.is_set() else "deny"

    def resolve(self, rid: str, allowed: bool, via: str = "click", choice: str | None = None) -> bool:
        """Answer a pending request. Voice answers are ignored for Tier 2. Returns True if applied."""
        with self._lock:
            pending = self._pending.get(rid)
            if pending is None or pending.done.is_set():
                return False
            if via == "voice" and pending.request.tier != Tier.CONFIRM:
                return False
            pending.allowed = bool(allowed)
            pending.via = via
            pending.choice = "allow" if allowed else (choice if choice == "alternative" else "deny")
            pending.done.set()
            return True

    def pending_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    def _listen_for_answer(self, rid: str) -> None:
        try:
            answer = parse_yes_no(self._listen() or "")
        except Exception as e:
            print(f"[Confirm] Voice answer failed: {e}")
            return
        if answer is not None:
            self.resolve(rid, answer, via="voice")
