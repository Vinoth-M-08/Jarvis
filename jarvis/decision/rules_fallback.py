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
    ("memory", r"\b(?:remember|forget|what do you know about me)\b"),
    ("chat_reply", r"\b(?:reply|respond to|answer (?:this|that) message|whatsapp|draft a (?:reply|response))\b"),
]
_QUESTION = re.compile(r"^(?:what|who|why|how|when|where|which|explain|define|describe|tell me|is|are|can|does|do)\b")

_DESTRUCTIVE = re.compile(r"\b(?:delete|remove|erase|wipe|overwrite|replace|move|rename|uninstall|clear|empty|purge|kill)\b")
_OUTWARD = re.compile(r"\b(?:send|post|publish|buy|pay|order|submit|transfer|email|tweet)\b")
_SENSITIVE_PLACES = re.compile(r"\b(?:c:[\\/]|program files|appdata|system32|windows folder|registry)\b")
_BULK = re.compile(r"\b(?:all|every|each|everything|organi[sz]e|sort|clean ?up|tidy|summari[sz]e|compare|research|find the best)\b")
_CLAUSES = re.compile(r"\b(?:and then|then|and also|after that|and)\b|,|;")

_VAGUE_OBJECT = re.compile(r"\b(?:it|that|this|them|those|these|stuff|things|old (?:files|stuff|things)|the files|some files)\b")
_CONCRETE = re.compile(r"[a-z]:[\\/]|\.\w{2,4}\b|\b(?:downloads|desktop|documents|pictures|music|videos)\b|\b\w+ folder\b")


def decide(text: str, recent_turn: bool = False, local_for_simple: bool = False,
           claude_complexity: int = 5, unclear_threshold: float = 0.7) -> Decision:
    t = text.lower().strip()
    d = Decision(source="rules")

    intents = [name for name, pattern in _INTENT_WORDS if re.search(pattern, t)]
    if not intents and _QUESTION.match(t):
        intents = ["question"]
    d.intents = intents
    d.intent = intents[0] if intents else "other"

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

    if d.unclear >= unclear_threshold:
        d.route = "ask"
    elif local_for_simple and d.complexity < claude_complexity and d.expected_risk < 6:
        d.route = "local"
    else:
        d.route = "claude"
    return d
