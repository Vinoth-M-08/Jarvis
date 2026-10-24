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


# Always-listening mode: these pause listening; saying "Jarvis" wakes it again.
SLEEP_PHRASES = {"stop listening", "you can stop listening", "go to sleep", "sleep", "sleep mode",
                 "pause listening", "take a break", "be quiet for a while", "don't listen", "mute the mic"}
# Said to wake it: nothing else to do after the name.
WAKE_ONLY = {"", "wake up", "are you there", "you there", "hello", "hi", "hey", "start listening",
             "listen", "i need you", "come back"}
_NAME = re.compile(r"\bj[ae]rvis\b", re.IGNORECASE)   # Whisper sometimes writes "Jervis"


def plain(text: str) -> str:
    """Lowercase, punctuation removed (apostrophes kept)."""
    return re.sub(r"[^\w' ]", "", text.lower()).strip()


def is_goodbye(text: str) -> bool:
    return plain(text) in GOODBYES


def is_filler(text: str) -> bool:
    return plain(text) in FILLERS


def is_sleep_command(text: str) -> bool:
    return plain(text) in SLEEP_PHRASES


def mentions_name(text: str) -> bool:
    return bool(_NAME.search(text))


def after_name(text: str) -> str:
    """What was said after the name: "okay so Jarvis, open Spotify" -> "open Spotify"."""
    m = _NAME.search(text)
    return text[m.end():].lstrip(" ,.!?") if m else text


def is_wake_only(text: str) -> bool:
    return plain(text) in WAKE_ONLY


def strip_wake_words(text: str) -> str:
    """'Hey Jarvis, what time is it' -> 'what time is it' (said while the conversation is open)."""
    return _WAKE_PREFIX.sub("", text, count=1).strip()


_WORD = re.compile(r"[a-z0-9']+")


def is_own_echo(heard: str, said: str, share: float = 0.7) -> bool:
    """Is `heard` mostly Jarvis's own recent words coming back through the mic? ("Sure, boss." /
    "I still need the dea-" were its own reply, taken as requests on 24 Oct.)"""
    words = _WORD.findall(heard.lower())
    if not words or not said:
        return False
    spoken = set(_WORD.findall(said.lower()))
    known = sum(1 for w in words if w in spoken or any(s.startswith(w) for s in spoken if len(w) >= 3))
    return known / len(words) >= share
