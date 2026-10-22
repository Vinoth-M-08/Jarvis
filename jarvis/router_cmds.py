"""Direct-command router: common commands run without any brain — 0 tokens (PLAN.md §4.1, Phase 2).

Only whole, simple utterances match ("open notepad", "volume 40", "what time is it").
Anything longer ("open chrome and search for ...") goes to the brain. Matched tool
calls still run through the gatekeeper.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

_FILLER = re.compile(r"^(?:hey\s+)?(?:jarvis[,\s]+)?(?:please\s+|can you\s+|could you\s+)?", re.IGNORECASE)
_TRAILING = re.compile(r"[\s.!?]*(?:please)?[\s.!?]*$", re.IGNORECASE)


@dataclass
class RouterMatch:
    tool: str | None = None            # tool to run through the gatekeeper
    args: dict = field(default_factory=dict)
    reply: str | None = None           # direct answer (no tool)


def normalize(text: str) -> str:
    t = _FILLER.sub("", text.strip())
    t = _TRAILING.sub("", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _time_reply(_m, now: datetime) -> RouterMatch:
    return RouterMatch(reply=f"It's {now.strftime('%I:%M %p').lstrip('0')}.")


def _date_reply(_m, now: datetime) -> RouterMatch:
    return RouterMatch(reply=f"Today is {now.strftime('%A, %B')} {now.day}, {now.year}.")


def _timer(m, _now) -> RouterMatch:
    n, unit = int(m.group("n")), m.group("unit")
    seconds = n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    return RouterMatch(tool="set_timer", args={"seconds": seconds, "message": f"{n} {unit} timer done"})


def _rules(app_names: set[str]) -> list[tuple[re.Pattern, Callable]]:
    apps = "|".join(sorted((re.escape(a) for a in app_names), key=len, reverse=True)) or "(?!)"
    r = lambda p: re.compile(rf"^(?:{p})$")
    return [
        (r(r"(?:what(?:'s| is) the time|what time is it|tell me the time|time)"), _time_reply),
        (r(r"(?:what(?:'s| is) (?:the )?(?:date|day)(?: today)?|what day is (?:it|today)|today'?s date)"), _date_reply),
        (r(rf"(?:open|launch|start|run) (?:the )?(?P<app>{apps})(?: app)?"),
         lambda m, _: RouterMatch(tool="open_app", args={"name": m.group("app")})),
        (r(r"(?:open|go to) (?P<site>[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|in|dev|ai|co|edu|gov)(?:/\S*)?)"),
         lambda m, _: RouterMatch(tool="open_url", args={"url": "https://" + m.group("site")})),
        (r(r"(?:set (?:the )?)?volume(?: to)? (?P<n>\d{1,3})(?: ?%| percent)?"),
         lambda m, _: RouterMatch(tool="set_volume", args={"level": min(100, int(m.group("n")))})),
        (r(r"(?:pause|stop)(?: the)?(?: music| song| video| playback)?"),
         lambda m, _: RouterMatch(tool="media_control", args={"action": "pause"})),
        (r(r"(?:play|resume)(?: the)?(?: music| song| video| playback)?"),
         lambda m, _: RouterMatch(tool="media_control", args={"action": "play"})),
        (r(r"(?:next|skip)(?: the)?(?: song| track| video)?"),
         lambda m, _: RouterMatch(tool="media_control", args={"action": "next"})),
        (r(r"(?:previous|last|go back a)(?: song| track| video)"),
         lambda m, _: RouterMatch(tool="media_control", args={"action": "previous"})),
        (r(r"mute (?:the )?(?:sound|audio|volume|speakers|music)"),
         lambda m, _: RouterMatch(tool="media_control", args={"action": "mute"})),
        (r(r"scroll (?P<dir>up|down)"),
         lambda m, _: RouterMatch(tool="scroll_screen", args={"direction": m.group("dir"), "amount": 3})),
        (r(r"lock (?:the |my )?(?:screen|computer|pc|laptop)"),
         lambda m, _: RouterMatch(tool="lock_screen", args={})),
        (r(r"take a screenshot|screenshot"),
         lambda m, _: RouterMatch(tool="screenshot", args={})),
        (r(r"(?:system info|system status|cpu usage|ram usage|memory usage|how much (?:ram|memory) (?:is )?(?:used|free))"),
         lambda m, _: RouterMatch(tool="get_system_info", args={})),
        (r(r"(?:what windows are open|list (?:the )?(?:open )?windows)"),
         lambda m, _: RouterMatch(tool="get_open_windows", args={})),
        (r(r"(?:set (?:a )?)?timer (?:for )?(?P<n>\d{1,4}) ?(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"),
         _timer),
    ]


class CommandRouter:
    def __init__(self, app_names: set[str]):
        self._rules = _rules({a.lower() for a in app_names})

    def match(self, text: str, now: datetime | None = None) -> RouterMatch | None:
        t = normalize(text)
        if not t:
            return None
        for pattern, build in self._rules:
            m = pattern.match(t)
            if m:
                return build(m, now or datetime.now())
        return None
