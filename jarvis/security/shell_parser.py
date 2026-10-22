"""Classify PowerShell commands for the gatekeeper (PLAN.md §5.4).

Not wired to a tool yet — the first shell tool arrives with Windows-MCP in Phase 3.
"""
from __future__ import annotations
import re
import shlex

from jarvis.security import paths
from jarvis.security.policy import Policy, Tier

DEFAULT_ALLOW = {
    "get-childitem", "get-content", "get-process", "start-process", "test-path",
    "measure-object", "get-date", "get-location", "select-object", "where-object",
    "sort-object", "format-table", "format-list",
}

# Anything matching these is hard-blocked (Tier 3).
DEFAULT_BLOCK_PATTERNS = [
    r"-enc(odedcommand)?\b",
    r"\binvoke-expression\b",
    r"\biex\b",
    r"\bformat(-volume)?\s",
    r"\bdiskpart\b",
    r"\bset-mppreference\b",
    r"\breg(\.exe)?\s+delete\b",
    r"\bbcdedit\b",
    r"\bvssadmin\b",
    r"\bstop-computer\b",
    r"\brestart-computer\b",
]

_SEPARATORS = re.compile(r"\|\||&&|[;|\n]")
_PATHISH = re.compile(r"^([A-Za-z]:|~)|[\\/]")
_ABSOLUTE = re.compile(r"^([A-Za-z]:[\\/]|~|[\\/])")


def split_commands(command: str) -> list[str]:
    return [part.strip() for part in _SEPARATORS.split(command) if part.strip()]


def classify_powershell(command: str, policy: Policy,
                        allow: set[str] = DEFAULT_ALLOW,
                        block: list[str] = DEFAULT_BLOCK_PATTERNS) -> tuple[Tier, str]:
    low = command.lower()
    for pattern in block:
        if re.search(pattern, low):
            return Tier.NEVER, f"blocked command pattern: {pattern}"
    if "`" in command:
        return Tier.STRONG, "backtick escapes hide what the command does"
    if "$(" in command:
        return Tier.STRONG, "subexpressions $( ) run hidden commands"
    if re.search(r"\b(cmd|powershell|pwsh)(\.exe)?\b", low):
        return Tier.STRONG, "nested shells are not inspected"

    tier, reason = Tier.FREE, "allowlisted read-only command"
    for part in split_commands(command):
        try:
            tokens = shlex.split(part, posix=False)
        except ValueError:
            return Tier.STRONG, "unbalanced quotes"
        if not tokens:
            continue
        name = tokens[0].lower()
        if name not in allow:
            tier, reason = max(tier, Tier.STRONG), f"'{tokens[0]}' is not on the allowlist"
        for tok in tokens[1:]:
            tok = tok.strip('"').strip("'")
            if tok.startswith("-") or not _PATHISH.search(tok):
                continue
            if not _ABSOLUTE.match(tok):
                tier, reason = max(tier, Tier.STRONG), f"relative path or URL '{tok}' can't be checked"
                continue
            try:
                target = paths.canonicalize(tok)
            except paths.UnsafePath as e:
                return Tier.NEVER, f"unsafe path {tok}: {e}"
            if paths.is_within(target, policy.blocked_paths):
                return Tier.NEVER, f"{target} is a protected location"
            if not paths.is_within(target, policy.free_paths) and tier < Tier.CONFIRM:
                tier, reason = Tier.CONFIRM, f"{target} is outside your free folders"
    return tier, reason
