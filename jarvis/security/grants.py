"""One-shot path grants: lets file_ops touch a path you just approved outside the free folders.

The gatekeeper opens a grant only for the exact approved path, only on the
thread running that one tool call, and closes it right after.
"""
from __future__ import annotations
import threading
from contextlib import contextmanager
from pathlib import Path

_local = threading.local()


@contextmanager
def allow_once(path: Path):
    _local.path = path
    try:
        yield
    finally:
        _local.path = None


def is_granted(path: Path) -> bool:
    return getattr(_local, "path", None) == path
