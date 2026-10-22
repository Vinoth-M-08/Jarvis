"""Spot and hide secrets before anything is saved to memory or task history.

Passwords, PINs, OTPs, card numbers, ID numbers and API keys must never land in
facts or task files (they'd be sent to the brain later as "context").
"""
from __future__ import annotations
import re

HIDDEN = "[hidden]"

# "my password is hunter2", "pin: 4821", "api key = sk-..." -> keep the words, hide the value.
# A separator (is / = / :) is required so "pin the window" or "pass the file" stay as they are.
_LABELLED = re.compile(
    r"(?P<label>\b(?:pass(?:word|code|phrase)|pwd|pin|otp|one[- ]time (?:password|code)|cvv|cvc|"
    r"security code|verification code|api[ _-]?key|access[ _-]?key|secret key|token|"
    r"account (?:number|no)|card (?:number|no)|upi pin|net ?banking (?:password|pin))\b"
    r"(?:\s+(?:for|of|to)\s+[\w.@-]+)?\s*(?:\bis\b|\bwas\b|=|:)\s*)"
    r"(?P<value>\S+)",
    re.IGNORECASE,
)

_PATTERNS = [
    re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),                    # card / account numbers (13-19 digits)
    re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),                   # Aadhaar
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),                      # PAN
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),           # API keys (sk-..., sk-ant-...)
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                        # AWS access key
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                    # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),   # JWT
]


def redact(text: str) -> str:
    """Replace anything that looks like a secret with [hidden]."""
    if not text:
        return text
    out = _LABELLED.sub(lambda m: m.group("label") + HIDDEN, text)
    for p in _PATTERNS:
        out = p.sub(HIDDEN, out)
    return out


def looks_secret(text: str) -> bool:
    return bool(text) and redact(text) != text
