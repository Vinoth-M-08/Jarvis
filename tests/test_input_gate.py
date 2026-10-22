# tests/test_input_gate.py
from unittest.mock import patch

import pytest

from jarvis.brain.usage import UsageTracker
from jarvis.input_gate import InputGate, forbidden_reason
from jarvis.security import account


@pytest.mark.parametrize("text", [
    "format the C drive",
    "Jarvis, format D:",
    "format my hard drive",
    "run diskpart",
    "delete the System32 folder",
    "remove everything in program files",
    "delete everything on my computer",
    "wipe my laptop",
    "disable Windows Defender",
    "turn off the firewall",
    "show me my saved passwords",
    "export all stored credentials",
    "read me my credit card number",
    "shut down the computer",
    "restart my pc",
])
def test_forbidden_requests_are_caught(text):
    assert forbidden_reason(text) is not None, text


@pytest.mark.parametrize("text", [
    "format this document as a table",
    "format my resume on the D drive nicely",
    "delete everything in my Downloads folder",
    "how do I reset my password?",
    "show me how to change my password",
    "shut down Chrome",
    "restart the music",
    "what is a firewall?",
    "open Windows Security settings",
    "list the files on my Desktop",
])
def test_harmless_requests_pass(text):
    assert forbidden_reason(text) is None, text


def test_refusal_uses_no_brain():
    gate = InputGate()
    d = gate.evaluate("format the C drive", "voice", uses_claude=True)
    assert d.refuse and "formatting a drive" in d.refusal_text()


def test_source_is_recorded():
    gate = InputGate()
    assert gate.evaluate("hi", "voice", uses_claude=False).can_act
    assert not gate.evaluate("hi", "timer", uses_claude=False).can_act


def test_budget_used_up_routes_to_local_brain():
    gate = InputGate(daily_claude_budget=10)
    d = gate.evaluate("hi", "voice", uses_claude=True, claude_requests_today=10, today="2026-10-22")
    assert d.use_local and "used up" in d.notice


def test_budget_warning_once_per_day():
    gate = InputGate(daily_claude_budget=10, warn_at_percent=80)
    assert gate.evaluate("hi", "voice", True, claude_requests_today=6, today="d1").notice == ""
    first = gate.evaluate("hi", "voice", True, claude_requests_today=7, today="d1")
    assert "8 of 10" in first.notice and not first.use_local
    assert gate.evaluate("hi", "voice", True, claude_requests_today=8, today="d1").notice == ""
    assert "8 of 10" in gate.evaluate("hi", "voice", True, claude_requests_today=7, today="d2").notice


def test_budget_ignored_for_local_provider_and_zero_budget():
    assert not InputGate(daily_claude_budget=1).evaluate("hi", "voice", False, 50).use_local
    assert not InputGate(daily_claude_budget=0).evaluate("hi", "voice", True, 50).use_local


def test_usage_tracker_counts_per_day(tmp_path):
    t = UsageTracker(str(tmp_path / "usage.db"))
    t.record(100, 20, 0, 1, day="2026-10-21")
    t.record(200, 30, 50, 2, day="2026-10-22")
    t.record(300, 40, 60, 1, day="2026-10-22")
    assert t.requests_on("2026-10-22") == 2
    assert t.requests_on("2026-10-21") == 1
    assert t.requests_on("2026-10-23") == 0


def test_account_warning_when_admin():
    with patch.object(account, "running_as_admin", return_value=True):
        assert "administrator" in account.account_warning()
    with patch.object(account, "running_as_admin", return_value=False):
        assert account.account_warning() is None
