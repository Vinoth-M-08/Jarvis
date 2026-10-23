"""Phrases for the open conversation (after "Hey Jarvis", keep talking without the wake word)."""
from __future__ import annotations
import re

# Ends an open conversation. Not "no" / "nothing": those answer Jarvis's own questions.
GOODBYES = {"thanks", "thank you", "thanks jarvis", "thank you jarvis", "that's all", "that's it",
            "that is all", "no thanks", "no thank you", "nothing else", "goodbye", "goodbye jarvis",
            "bye", "bye jarvis",
            "all good", "i'm good", "we're done", "stop listening", "you can stop listening",
            "go to sleep", "that's all for now"}

# Sounds Whisper turns into words, or filler — ignored while a conversation is open.
FILLERS = {"", "um", "uh", "hmm", "mm", "mhm", "ah", "oh", "you", "so", "thanks for watching"}

_WAKE_PREFIX = re.compile(r"^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?jarvis\b[\s,.!?]*", re.IGNORECASE)


def plain(text: str) -> str:
    """Lowercase, punctuation removed (apostrophes kept)."""
    return re.sub(r"[^\w' ]", "", text.lower()).strip()


def is_goodbye(text: str) -> bool:
    return plain(text) in GOODBYES


def is_filler(text: str) -> bool:
    return plain(text) in FILLERS


def strip_wake_words(text: str) -> str:
    """'Hey Jarvis, what time is it' -> 'what time is it' (said while the conversation is open)."""
    return _WAKE_PREFIX.sub("", text, count=1).strip()
