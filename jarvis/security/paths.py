"""Path canonicalization for the gatekeeper (PLAN.md §5.4).

Every path is turned into one absolute, resolved form before it is compared,
so tricks like `free\\..\\C:\\Windows`, symlinks, junctions and 8.3 short names
(resolve() expands them for folders that exist) all land on the real target.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

_DRIVE = re.compile(r"^[A-Za-z]:")


class UnsafePath(ValueError):
    """Path is malformed or uses a form the gatekeeper refuses to reason about."""


def canonicalize(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise UnsafePath("empty path")
    raw = path.strip().strip('"').strip("'")
    if raw.startswith(("\\\\", "//")):
        raise UnsafePath("network and device paths (\\\\server, \\\\?\\, \\\\.\\) are not allowed")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    rest = expanded[2:] if _DRIVE.match(expanded) else expanded
    if ":" in rest:
        raise UnsafePath("alternate data streams (file.txt:stream) are not allowed")
    p = Path(expanded)
    if not p.is_absolute():
        raise UnsafePath("use a full path, e.g. C:\\Users\\you\\Documents\\file.txt")
    return p.resolve()


def is_within(target: Path, dirs: list[str]) -> bool:
    """True if target equals or sits inside one of dirs (component-wise, not string prefix)."""
    for d in dirs:
        try:
            base = canonicalize(d)
        except UnsafePath:
            continue
        if target == base or base in target.parents:
            return True
    return False
