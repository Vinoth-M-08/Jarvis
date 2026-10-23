# tests/test_selector.py
from datetime import datetime, timedelta, timezone

from jarvis.brain.selector import BrainSelector, LocalBackup, classify_failure
from jarvis.brain.usage import UsageTracker
from jarvis.notify import Notifier


def test_classify_failure():
    assert classify_failure("Claude run failed: usage limit reached") == "limit"
    assert classify_failure("API error 429 rate_limit_error") == "limit"
    assert classify_failure("Invalid API key · Please run /login") == "auth"
    assert classify_failure("Connection refused") == "unavailable"


def test_selector_backs_off_after_failure_and_recovers():
    now = [0.0]
    sel = BrainSelector(limit_backoff_s=900, error_backoff_s=120, clock=lambda: now[0])
    assert sel.choose(uses_claude=True) == "claude"
    notice = sel.claude_failed(RuntimeError("usage limit reached"))
    assert "limit" in notice
    assert sel.choose(True) == "local"
    assert sel.claude_failed(RuntimeError("usage limit reached")) is None  # said once
    now[0] += 901
    assert sel.choose(True) == "claude"
    assert sel.claude_ok() is True     # came back -> unload local model
    assert sel.claude_ok() is False


def test_selector_respects_budget_and_local_provider():
    sel = BrainSelector()
    assert sel.choose(uses_claude=True, budget_used_up=True) == "local"
    assert sel.choose(uses_claude=False) == "local"


def test_short_backoff_for_connection_errors():
    now = [0.0]
    sel = BrainSelector(limit_backoff_s=900, error_backoff_s=120, clock=lambda: now[0])
    sel.claude_failed(RuntimeError("connection refused"))
    now[0] += 121
    assert sel.choose(True) == "claude"


def test_local_backup_checks_vram_before_loading():
    unloaded = []
    free = [2.0]
    backup = LocalBackup("qwen3:4b", min_free_vram_gb=4, free_vram_gb=lambda: free[0], unload_fn=unloaded.append,
                         is_resident=lambda m: False)
    ok, why = backup.ready()
    assert not ok and "GPU is busy" in why
    free[0] = 6.0
    assert backup.ready() == (True, "")
    backup.mark_used()
    free[0] = 1.0
    assert backup.ready()[0]      # already loaded: its own VRAM is counted as used
    backup.unload()
    assert unloaded == ["qwen3:4b"] and not backup.loaded
    backup.unload()
    assert unloaded == ["qwen3:4b"]  # no double unload


def test_local_backup_without_gpu_info_is_ready():
    assert LocalBackup("m", free_vram_gb=lambda: None, is_resident=lambda m: False).ready() == (True, "")


def test_notifier_batches_routine_and_speaks_urgent():
    spoken, events = [], []
    n = Notifier(speak=spoken.append, broadcast=events.append, is_busy=lambda: False)
    n.urgent("Timer done: tea.")
    assert spoken == ["Timer done: tea."]
    n.routine("Full access ended.")
    n.routine("Download finished.")
    assert n.pending() == 2 and len(spoken) == 1
    assert n.flush() == "Updates: Full access ended. Download finished."
    assert n.pending() == 0 and n.flush() is None
    assert [e["level"] for e in events] == ["urgent", "routine", "routine"]


def test_usage_windows(tmp_path):
    t = UsageTracker(str(tmp_path / "u.db"))
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    t.record(100, 10, 0, 1, ts=now - timedelta(hours=1))
    t.record(200, 20, 50, 1, ts=now - timedelta(hours=6))
    t.record(300, 30, 0, 1, ts=now - timedelta(days=8))
    five = t.window(5, now=now)
    week = t.window(24 * 7, now=now)
    assert five == {"requests": 1, "input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 0}
    assert week["requests"] == 2 and week["input_tokens"] == 300 and week["cache_read_input_tokens"] == 50


def test_model_already_in_gpu_memory_is_ready_even_if_little_is_free():
    """Its own 3.3 GB must not count against it (fast mode refused with the model loaded)."""
    backup = LocalBackup("qwen3:4b-q4_K_M", min_free_vram_gb=4, free_vram_gb=lambda: 2.8,
                         is_resident=lambda m: m == "qwen3:4b-q4_K_M")
    assert backup.ready() == (True, "") and backup.loaded
    cold = LocalBackup("qwen3:4b-q4_K_M", min_free_vram_gb=4, free_vram_gb=lambda: 2.8, is_resident=lambda m: False)
    assert cold.ready()[0] is False
