"""Direct-command router: common commands run without any brain — 0 tokens (PLAN.md §4.1, Phase 2).

Only whole, simple utterances match ("open notepad", "volume 40", "what time is it").
Anything longer ("open chrome and search for ...") goes to the brain. Matched tool
calls still run through the gatekeeper.

Jarvis's own settings (brain, Claude model, mute, voice speed) are router-only: no brain tool can
change them, so a web page or file can't talk Claude into switching brains.
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
    setting: str | None = None         # Jarvis setting: brain | claude_model | which_brain | mute | speed | forget_task | fast_chat | apply_status | apply_run | apply_questions
    value: object = None               # brain: "claude"/"local"; claude_model: "opus"/"sonnet"/"haiku";
                                       # mute: bool; speed: "faster"/"slower"/"normal"/float


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


# Includes how Whisper writes the names: "Olamma", "llama", "cloud" (for Claude).
_BRAIN = r"(?P<brain>claude|cloud|local|ollama|olama|olamma|llama|backup|offline)(?: brain| model| ai| one)?"
_BRAIN_WORDS = {"claude": "claude", "cloud": "claude", "local": "local", "ollama": "local", "olama": "local",
                "olamma": "local", "llama": "local", "backup": "local", "offline": "local"}
# "switch from Claude to Ollama", "change the model to local", "switch over to the Ollama model"
_SWITCH_TO = (r"(?:(?:i want (?:you )?|i'd like (?:you )?|i need (?:you )?)?to )?"   # "(I want) to switch..."
              r"(?:switch|change|go|move|swap)(?: back)?(?: (?:the|your) (?:brain|model|ai))?"
              r"(?: from (?:the )?\w+(?: brain| model| ai)?)?(?: over)? to (?:the |your )?")


def _brain(m, _now) -> RouterMatch:
    return RouterMatch(setting="brain", value=_BRAIN_WORDS[m.group("brain")])


def _speed_step(m, _now) -> RouterMatch:
    slower = (m.group("dir") or m.group("dir2")) in ("slower", "slow down")
    return RouterMatch(setting="speed", value="slower" if slower else "faster")


_CLAUDE_MODEL = r"(?:claude )?(?P<cm>opus|sonnet|haiku)(?: model)?"


def _setting_rules(r) -> list[tuple[re.Pattern, Callable]]:
    claude_model = lambda m, _: RouterMatch(setting="claude_model", value=m.group("cm"))
    return [
        (r(r"(?:forget|delete) (?:that|the last|my last|the previous|my previous) task|forget what (?:i|we) just did"),
         lambda m, _: RouterMatch(setting="forget_task", value="last")),
        (r(r"forget (?:today|today's tasks|what i did today|everything (?:from|i did) today)"),
         lambda m, _: RouterMatch(setting="forget_task", value="today")),
        (r(rf"(?:switch|change|go)(?: back)? to (?:the |your )?{_CLAUDE_MODEL}"), claude_model),
        (r(rf"use (?:the |your )?{_CLAUDE_MODEL}"), claude_model),
        (r(rf"{_SWITCH_TO}{_BRAIN}"), _brain),
        (r(rf"use (?:the |your )?{_BRAIN}"), _brain),
        (r(r"(?:which|what) (?:brain|model|ai)(?: are you using| are you on| is (?:active|on))?"),
         lambda m, _: RouterMatch(setting="which_brain")),
        (r(r"(?:ask me|go through|let'?s (?:do|go through|answer)|answer) (?:your|the|my|all (?:the|your|my)) "
           r"(?:job |application )?questions(?: now)?|ask me what you need"),
         lambda m, _: RouterMatch(setting="apply_questions")),
        (r(r"(?:check|look for|find) (?:for )?(?:the next )?(?:(?P<n>\d+) )?new jobs|"
           r"prepare (?:my |the )?(?:job )?applications|(?:start|run) (?:the )?(?:job check|applications?|applying)|"
           r"apply (?:to|for) (?:new |the )?jobs"),
         lambda m, _: RouterMatch(setting="apply_run", value=int(m.group("n")) if m.group("n") else None)),
        (r(r"what do you need(?: from me)?|(?:do you have )?any questions for me|"
           r"how are (?:my|the) (?:job )?applications(?: going)?|(?:job )?application status"),
         lambda m, _: RouterMatch(setting="apply_status")),
        (r(r"(?:turn on |switch on |enable |start )?fast (?:mode|chat|replies|answers)(?: on)?"),
         lambda m, _: RouterMatch(setting="fast_chat", value=True)),
        (r(r"(?:turn off |switch off |disable |stop )fast (?:mode|chat|replies|answers)|fast (?:mode|chat|replies|answers) off"),
         lambda m, _: RouterMatch(setting="fast_chat", value=False)),
        (r(r"mute (?:yourself|your voice|jarvis)|(?:go )?silent(?: mode)?|text only(?: mode)?"),
         lambda m, _: RouterMatch(setting="mute", value=True)),
        (r(r"unmute(?: yourself| your voice| jarvis)?|(?:talk|speak) (?:to me )?again|you can (?:talk|speak) (?:again|now)|voice on"),
         lambda m, _: RouterMatch(setting="mute", value=False)),
        (r(r"(?:speak|talk) (?P<dir>faster|slower)|(?P<dir2>speed up|slow down)(?: your voice| a bit| a little)?"),
         _speed_step),
        (r(r"(?:set )?(?:your )?(?:voice |speaking |talking )?speed(?: to)? (?P<n>[0-2](?:\.\d{1,2})?)"),
         lambda m, _: RouterMatch(setting="speed", value=float(m.group("n")))),
        (r(r"(?:speak|talk) (?:at )?normal(?:ly| speed)?|normal (?:voice )?speed|reset (?:your )?(?:voice )?speed"),
         lambda m, _: RouterMatch(setting="speed", value="normal")),
    ]


def _rules(app_names: set[str]) -> list[tuple[re.Pattern, Callable]]:
    apps = "|".join(sorted((re.escape(a) for a in app_names), key=len, reverse=True)) or "(?!)"
    r = lambda p: re.compile(rf"^(?:{p})$")
    return _setting_rules(r) + [
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
