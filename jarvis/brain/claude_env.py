"""Keep the Claude brain on your subscription login (PLAN.md §4.4).

Claude Code prefers an API key over the subscription login when one is set, so a
stray ANTHROPIC_API_KEY would silently bill the API instead. Before Jarvis starts
a Claude process, these variables are removed from its environment.

Only variables that change *who pays or which backend is used* are removed.
Other CLAUDE_CODE_* settings (e.g. CLAUDE_CODE_GIT_BASH_PATH, which Claude Code
needs on Windows) are kept.
"""
from __future__ import annotations
import os

STRIP_PREFIXES = ("ANTHROPIC_",)
STRIP_NAMES = {
    # switch Claude Code to another billing backend
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    # set when Jarvis itself was started from inside a Claude Code session
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
}


def _should_strip(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(STRIP_PREFIXES) or upper in STRIP_NAMES


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment without the billing/backend variables."""
    env = dict(os.environ if base is None else base)
    return {k: v for k, v in env.items() if not _should_strip(k)}


def scrub_process_env() -> list[str]:
    """Remove the variables from this process, so every child (the Claude Code CLI) inherits a clean env.

    Returns the names removed (never the values) for the startup log.
    """
    removed = [k for k in list(os.environ) if _should_strip(k)]
    for k in removed:
        os.environ.pop(k, None)
    return sorted(removed)
