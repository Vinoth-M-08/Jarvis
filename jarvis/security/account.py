"""Startup check: warn when Jarvis runs with administrator rights (PLAN.md §5.4).

Jarvis should run as a standard (non-admin) Windows user — see docs/NON_ADMIN_ACCOUNT.md.
Then even a gatekeeper bug can't touch Windows or other users' files.
"""
from __future__ import annotations
import getpass
import sys


def running_as_admin() -> bool:
    if sys.platform != "win32":
        import os
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def account_warning() -> str | None:
    """A one-line warning for the console/HUD, or None when the account looks right."""
    if running_as_admin():
        return (f"Jarvis is running with administrator rights (user '{getpass.getuser()}'). "
                "Run it as a standard user — see docs/NON_ADMIN_ACCOUNT.md.")
    return None
