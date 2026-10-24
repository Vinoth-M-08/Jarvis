"""Rules + keyword fallback for the decision maker — free, local, instant (PLAN.md §4.8).

Simpler than the Jev model, but answers the same typed questions.
"""
from __future__ import annotations
import re

from jarvis.decision.questions import Decision

_INTENT_WORDS: list[tuple[str, str]] = [
    ("file", r"\b(?:files?|folders?|documents?|downloads|desktop|pdf|docx?|txt|csv|rename|copy|save|write|notes?)\b|[a-z]:[\\/]|\.\w{2,4}\b"),
    ("browser", r"\b(?:search|google|website|web|browse|youtube|url|site|look up|online|internet)\b"),
    ("app_window", r"\b(?:open|launch|close|window|windows|app|switch to|focus|minimi[sz]e|maximi[sz]e|click|type)\b"),
    ("system", r"\b(?:volume|brightness|battery|cpu|ram|memory usage|lock|timer|reminder|notification|clipboard|mute)\b"),
    ("memory", r"\b(?:remember|forget|what do you know about me|last (?:week|month|time)|yesterday|days? ago|"
               r"did (?:i|you|we)|when did|have (?:i|you) (?:ever|already))\b"),
    ("chat_reply", r"\b(?:reply|respond to|answer (?:this|that) message|whatsapp|draft a (?:reply|response))\b"),
]
_CHAT_INTENTS = {"question"}   # (no intent at all = small talk, also chat)
# Work that means looking at and driving the screen — the local 4B model fumbles these
# ("open new tab" -> opened Chrome; "open Notepad and type hello" -> tried to focus it first).
_SCREEN_WORK = re.compile(r"\b(?:type|typing|click|press|scroll|drag|select|fill|tabs?|play|playing|navigate|"
                          r"log ?in|sign ?in|compose|reply|inbox|mail|email|message|checkout)\b")
# An order, after any "can you / please / I need you to": something to do, not something to chat about.
_COMMAND = re.compile(r"^(?:(?:hey|ok(?:ay)?|so|now|jarvis)[,!.]?\s+)*(?:please\s+|(?:can|could|would) you\s+(?:please\s+)?|"
                      r"i (?:need|want) you to\s+|go ahead and\s+)?"
                      r"(?:take|show|check|find|start|make|create|download|install|move|put|get|record|turn|enable|"
                      r"disable|close|minimi[sz]e|maximi[sz]e|restart|shut|run|play|pause|copy|paste|press|"
                      r"pull up|bring up|navigate|visit|read|fetch|look up|look for)\b")
_QUESTION = re.compile(r"^(?:what|who|why|how|when|where|which|explain|define|describe|tell me|is|are|can|does|do)\b")

_DESTRUCTIVE = re.compile(r"\b(?:delete|remove|erase|wipe|overwrite|replace|move|rename|uninstall|clear|empty|purge|kill)\b")
_OUTWARD = re.compile(r"\b(?:send|post|publish|buy|pay|order|submit|transfer|email|tweet)\b")
_SENSITIVE_PLACES = re.compile(r"\b(?:c:[\\/]|program files|appdata|system32|windows folder|registry)\b")
_BULK = re.compile(r"\b(?:all|every|each|everything|organi[sz]e|sort|clean ?up|tidy|summari[sz]e|compare|research|find the best)\b")
_CLAUSES = re.compile(r"\b(?:and then|then|and also|after that|and)\b|,|;")

_VAGUE_OBJECT = re.compile(r"\b(?:it|that|this|them|those|these|stuff|things|old (?:files|stuff|things)|the files|some files)\b")
_CONCRETE = re.compile(r"[a-z]:[\\/]|\.\w{2,4}\b|\b(?:downloads|desktop|documents|pictures|music|videos)\b|\b\w+ folder\b")


# The local brain saying it will do something instead of doing it ("I'll open the LMS project for
# you. Let me locate it on the desktop." — and no tool call): Claude does it then.
_PROMISE = re.compile(r"\b(?:i'll|i will|i'm going to|i am going to|let me)\s+(?:now\s+|quickly\s+|just\s+|go\s+ahead\s+and\s+)?"
                      r"(?:open|check|look|find|search|locate|launch|start|go|navigate|click|type|get|take|play|"
                      r"close|switch|show|pull|bring|run|send|read|scroll|fetch|install|create|set)\b|"
                      r"^(?:sure[,!.]?\s+|okay[,!.]?\s+|alright[,!.]?\s+)?(?:opening|launching|checking|searching)\b",
                      re.IGNORECASE)


def promises_action(reply: str) -> bool:
    return bool(_PROMISE.search(reply.strip()))


def decide(text: str, recent_turn: bool = False, local_for_simple: bool = False,
           claude_complexity: int = 5, unclear_threshold: float = 0.7) -> Decision:
    t = text.lower().strip()
    d = Decision(source="rules")

    intents = [name for name, pattern in _INTENT_WORDS if re.search(pattern, t)]
    if not intents and _QUESTION.match(t):
        intents = ["question"]
    d.intents = intents
    d.intent = intents[0] if intents else "other"
    d.command = bool(_COMMAND.match(t))

    risk = 2
    if _DESTRUCTIVE.search(t):
        risk += 5
    if _OUTWARD.search(t):
        risk += 4
    if _SENSITIVE_PLACES.search(t):
        risk += 2
    d.expected_risk = min(10, risk)

    clauses = len(_CLAUSES.findall(t))
    complexity = 2 + 2 * clauses + (2 if _BULK.search(t) else 0) + (1 if len(t.split()) > 15 else 0)
    d.complexity = min(10, complexity)

    # Vague destructive request with nothing concrete to point at. Skipped right after
    # another turn, because "delete them" can refer to what was just listed.
    if not recent_turn and _DESTRUCTIVE.search(t) and _VAGUE_OBJECT.search(t) and not _CONCRETE.search(t):
        d.unclear = 0.85
        d.clarify_question = "Which files or folder do you mean? Tell me the folder name or the full path."

    if d.complexity >= 6 or (d.expected_risk >= 6 and d.intent == "file") or _BULK.search(t) and _DESTRUCTIVE.search(t):
        d.needs_plan = 0.8
    elif d.expected_risk >= 6:
        d.needs_plan = 0.6
    else:
        d.needs_plan = 0.1

    d.multi_step = clauses > 0 or bool(_BULK.search(t)) or bool(_SCREEN_WORK.search(t)) or d.needs_plan >= 0.5

    if d.unclear >= unclear_threshold:
        d.route = "ask"
    elif (local_for_simple and set(d.intents) <= _CHAT_INTENTS
          and d.complexity < claude_complexity and d.expected_risk < 6):
        d.route = "local"   # fast mode: plain chat / questions only — tasks with tools stay on Claude
    else:
        d.route = "claude"
    return d
