"""Security policy: tiers, free/blocked paths, untrusted tools (PLAN.md §5)."""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


class Tier(IntEnum):
    FREE = 0      # runs immediately
    CONFIRM = 1   # voice "yes" or click
    STRONG = 2    # click every time
    NEVER = 3     # hard-blocked, no override


DEFAULT_BLOCKED_PATHS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    "~/AppData/Roaming/Microsoft/Credentials",
    "~/AppData/Local/Microsoft/Credentials",
    "~/AppData/Roaming/Microsoft/Protect",
]

# Tools whose output can carry text written by someone else (web pages, screen, clipboard).
DEFAULT_UNTRUSTED_TOOLS = ["web_search", "fetch_page", "read_screen", "find_on_screen", "get_clipboard"]


@dataclass
class Policy:
    free_paths: list[str]
    blocked_paths: list[str]
    untrusted_tools: set[str]
    max_denials_per_turn: int = 2
    audit_db: str = "data/audit.db"
    confirm_timeout_s: float = 30.0
    elevation_max_minutes: int = 15


def load_policy(cfg: dict | None = None) -> Policy:
    """Build the policy from config.yaml. Free paths are tools.allowed_paths."""
    if cfg is None:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
    sec = cfg.get("security") or {}
    return Policy(
        free_paths=list((cfg.get("tools") or {}).get("allowed_paths", [])),
        blocked_paths=list(sec.get("blocked_paths", DEFAULT_BLOCKED_PATHS)),
        untrusted_tools=set(sec.get("untrusted_tools", DEFAULT_UNTRUSTED_TOOLS)),
        max_denials_per_turn=int(sec.get("max_denials_per_turn", 2)),
        audit_db=sec.get("audit_db", "data/audit.db"),
        confirm_timeout_s=float(sec.get("confirm_timeout_s", 30)),
        elevation_max_minutes=int(sec.get("elevation_max_minutes", 15)),
    )
