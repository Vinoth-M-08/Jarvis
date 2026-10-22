"""The typed questions the decision maker answers for every request (PLAN.md §4.8).

Written as data so the Jev client can send them as-is (Choice / Score / Noul), and
the rules fallback answers the same questions.
"""
from __future__ import annotations
from dataclasses import dataclass, field

INTENTS = ["file", "app_window", "system", "browser", "question", "chat_reply", "memory", "other"]

# Tool trimming: the brain only gets the tools a request needs (local brain; prompt size).
TOOL_SETS: dict[str, list[str]] = {
    "file": ["list_files", "read_file", "write_file", "delegate_task"],
    "app_window": ["open_app", "focus_window", "get_open_windows", "read_screen", "find_on_screen",
                   "click_at", "type_text", "press_key", "scroll_screen", "move_mouse", "screenshot",
                   "win_snapshot", "win_click", "win_type", "win_scroll", "win_move", "win_shortcut",
                   "win_wait", "win_waitfor", "win_app", "win_powershell"],
    "system": ["set_volume", "get_volume", "media_control", "get_system_info", "set_brightness",
               "lock_screen", "show_notification", "set_timer", "get_clipboard", "set_clipboard"],
    "browser": ["web_search", "fetch_page", "open_url", "get_weather",
                "browser_open", "browser_search", "browser_read", "browser_click", "browser_type",
                "browser_scroll", "browser_back", "browser_tabs"],
    "question": ["web_search", "fetch_page", "get_weather", "delegate_task", "browser_search", "browser_read"],
    "chat_reply": ["focus_window", "read_screen", "find_on_screen", "click_at", "type_text",
                   "win_app", "win_snapshot", "win_click", "win_type"],
    "memory": [],
    "other": [],  # empty = all tools
}

ROUTES = ["claude", "local", "ask", "refuse"]


@dataclass
class TypedQuestion:
    name: str
    kind: str                  # "choice" | "score" | "noul"
    prompt: str
    options: list[str] = field(default_factory=list)   # choice only
    levels: tuple[int, int] = (2, 10)                  # score only


QUESTIONS = [
    TypedQuestion("route", "choice", "Which brain should handle this request?", ROUTES),
    TypedQuestion("intent", "choice", "What kind of request is this?", INTENTS),
    TypedQuestion("expected_risk", "score", "How risky is this request for the owner's files, accounts or PC?"),
    TypedQuestion("complexity", "score", "How many steps or how much reasoning does it need?"),
    TypedQuestion("unclear", "noul", "Is the request too unclear to act on without asking?"),
    TypedQuestion("needs_plan", "noul", "Does it have several steps or change files, so a plan should be approved first?"),
]


@dataclass
class Decision:
    route: str = "claude"
    intent: str = "other"
    intents: list[str] = field(default_factory=list)
    expected_risk: int = 2
    complexity: int = 2
    unclear: float = 0.0
    needs_plan: float = 0.0
    clarify_question: str = ""
    source: str = "rules"      # "rules" | "jev"

    def tool_names(self) -> set[str] | None:
        """Tools for this request; None = all."""
        names: set[str] = set()
        for intent in self.intents or [self.intent]:
            tools = TOOL_SETS.get(intent, [])
            if not tools:
                return None
            names.update(tools)
        return names or None

    def as_event(self) -> dict:
        return {"type": "decision", "route": self.route, "intent": self.intent, "intents": self.intents,
                "expected_risk": self.expected_risk, "complexity": self.complexity,
                "unclear": round(self.unclear, 2), "needs_plan": round(self.needs_plan, 2), "source": self.source}
