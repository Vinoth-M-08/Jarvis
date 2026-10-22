# tests/test_confirm.py
import threading
import time
from unittest.mock import patch

import pytest

from jarvis.security import grants, paths
from jarvis.security.audit import AuditLog
from jarvis.security.confirm import ConfirmBroker, describe, parse_yes_no
from jarvis.security.elevation import Elevation, parse_command
from jarvis.security.gatekeeper import ConfirmRequest, Gatekeeper, TurnContext
from jarvis.security.policy import DEFAULT_UNTRUSTED_TOOLS, Policy, Tier


def _req(tier=Tier.CONFIRM, tool="read_file", args=None):
    return ConfirmRequest(TurnContext("voice"), tool, args or {"path": "C:/x.txt"}, tier, "outside free folders")


def _ask_in_thread(broker, req):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("allowed", broker.ask(req)))
    t.start()
    return t, out


def _wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# --- parsing / text ---

@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yeah, go ahead.", True), ("okay", True), ("allow it", True),
    ("no", False), ("No, don't.", False), ("cancel", False), ("yes... actually no", False),
    ("what?", None), ("", None),
])
def test_parse_yes_no(text, expected):
    assert parse_yes_no(text) is expected


def test_describe():
    assert describe("read_file", {"path": "C:/a.txt"}) == "read file C:/a.txt"
    assert describe("press_key", {"keys": "alt+f4"}) == "press alt+f4"
    assert describe("full_access", {"minutes": 10}) == "turn on full access for 10 minutes"
    assert "…" in describe("type_text", {"text": "x" * 100})


# --- broker ---

def test_click_allow_resolves_and_broadcasts():
    events = []
    broker = ConfirmBroker(broadcast=events.append, timeout_s=5)
    t, out = _ask_in_thread(broker, _req())
    assert _wait_for(lambda: broker.pending_ids())
    rid = broker.pending_ids()[0]
    assert broker.resolve(rid, True, via="click")
    t.join(2)
    assert out["allowed"] is True
    assert events[0]["type"] == "confirm_request" and events[0]["voice_ok"] is True
    assert events[-1] == {"type": "confirm_resolved", "id": rid, "allowed": True, "via": "click"}


def test_timeout_means_deny():
    events = []
    broker = ConfirmBroker(broadcast=events.append, timeout_s=0.5)
    assert broker.ask(_req()) is False
    assert events[-1]["via"] == "timeout" and events[-1]["allowed"] is False
    assert broker.pending_ids() == []


def test_abort_means_deny():
    abort = threading.Event()
    broker = ConfirmBroker(broadcast=lambda e: None, abort_event=abort, timeout_s=10)
    t, out = _ask_in_thread(broker, _req())
    assert _wait_for(lambda: broker.pending_ids())
    abort.set()
    t.join(2)
    assert out["allowed"] is False


def test_voice_answer_ignored_for_strong_tier():
    broker = ConfirmBroker(broadcast=lambda e: None, timeout_s=5)
    t, out = _ask_in_thread(broker, _req(tier=Tier.STRONG))
    assert _wait_for(lambda: broker.pending_ids())
    rid = broker.pending_ids()[0]
    assert broker.resolve(rid, True, via="voice") is False
    assert broker.resolve(rid, False, via="click") is True
    t.join(2)
    assert out["allowed"] is False


def test_spoken_yes_answers_tier1():
    spoken = []
    broker = ConfirmBroker(broadcast=lambda e: None, speak=spoken.append,
                           listen=lambda: "yes please", timeout_s=5)
    assert broker.ask(_req()) is True
    assert "Say yes or no" in spoken[0]


def test_strong_tier_does_not_listen():
    listened = []
    spoken = []
    broker = ConfirmBroker(broadcast=lambda e: None, speak=spoken.append,
                           listen=lambda: listened.append(1) or "yes", timeout_s=0.5)
    assert broker.ask(_req(tier=Tier.STRONG)) is False
    assert listened == []
    assert "click" in spoken[0].lower()


def test_resolve_unknown_id_is_ignored():
    broker = ConfirmBroker(broadcast=lambda e: None)
    assert broker.resolve("nope", True) is False


# --- elevation ---

@pytest.mark.parametrize("text,expected", [
    ("Jarvis, full access for 10 minutes", ("grant", 10)),
    ("give me full access for 5 min", ("grant", 5)),
    ("full access 3 minutes", ("grant", 3)),
    ("end full access", ("revoke", 0)),
    ("full access off", ("revoke", 0)),
    ("what is full access?", None),
    ("open notepad", None),
])
def test_parse_elevation_command(text, expected):
    assert parse_command(text) == expected


def test_elevation_caps_and_expires():
    now = [1000.0]
    changes = []
    elev = Elevation(max_minutes=15, on_change=lambda a, r: changes.append(a), clock=lambda: now[0])
    assert elev.grant(60) == 15
    assert elev.active() and elev.remaining_s() == 900
    now[0] += 901
    assert not elev.active()
    elev.revoke()  # cancels the timer thread
    assert changes[0] is True


def test_revoke_ends_elevation():
    elev = Elevation(max_minutes=15)
    elev.grant(5)
    elev.revoke()
    assert not elev.active()


# --- gatekeeper + elevation + grants ---

@pytest.fixture
def setup(tmp_path):
    free, other = tmp_path / "free", tmp_path / "other"
    free.mkdir()
    other.mkdir()
    policy = Policy(free_paths=[str(free)], blocked_paths=[], untrusted_tools=set(DEFAULT_UNTRUSTED_TOOLS),
                    audit_db=str(tmp_path / "audit.db"))
    asked = []
    gk = Gatekeeper(policy_loader=lambda: policy, confirmer=lambda r: asked.append(r.tier) or False,
                    audit=AuditLog(policy.audit_db))
    return gk, free, other, asked


def test_elevation_skips_tier1_asks(setup):
    gk, _, other, asked = setup
    gk.elevation.grant(5)
    turn = gk.new_turn("voice")
    assert gk.run(turn, "read_file", {"path": str(other / "x")}, lambda t, a: "ok") == "ok"
    assert asked == []
    gk.elevation.revoke()


def test_elevation_never_skips_strong_tier(setup):
    gk, free, _, asked = setup
    (free / "a.txt").write_text("old")
    gk.elevation.grant(5)
    turn = gk.new_turn("voice")
    result = gk.run(turn, "write_file", {"path": str(free / "a.txt"), "content": "new"}, lambda t, a: "ok")
    assert result.startswith("BLOCKED") and asked == [Tier.STRONG]
    gk.elevation.revoke()


def test_elevation_never_skips_taint_gate(setup):
    gk, free, _, asked = setup
    gk.elevation.grant(5)
    turn = gk.new_turn("voice")
    gk.run(turn, "read_screen", {}, lambda t, a: "screen text")
    result = gk.run(turn, "write_file", {"path": str(free / "b.txt"), "content": "x"}, lambda t, a: "ok")
    assert result.startswith("BLOCKED") and asked == [Tier.CONFIRM]
    gk.elevation.revoke()


def test_approved_outside_path_is_granted_once_to_file_ops(setup, tmp_path):
    gk, free, other, _ = setup
    target = other / "notes.txt"
    target.write_text("hello")
    gk.confirmer = lambda r: True
    cfg = {"tools": {"allowed_paths": [str(free)]}}
    with patch("jarvis.tools.file_ops._load_config", return_value=cfg):
        from jarvis.tools.file_ops import read_file
        from jarvis.tools.router import dispatch
        turn = gk.new_turn("voice")
        assert gk.run(turn, "read_file", {"path": str(target)}, dispatch) == "hello"
        with pytest.raises(PermissionError):
            read_file(str(target))  # grant is gone after the call


def test_grant_is_exact_path_only(tmp_path):
    a = paths.canonicalize(str(tmp_path / "a.txt"))
    b = paths.canonicalize(str(tmp_path / "b.txt"))
    with grants.allow_once(a):
        assert grants.is_granted(a)
        assert not grants.is_granted(b)
    assert not grants.is_granted(a)


def test_file_ops_expands_home_folder(tmp_path):
    cfg = {"tools": {"allowed_paths": ["~/JarvisTestFree"]}}
    with patch("jarvis.tools.file_ops._load_config", return_value=cfg), \
         patch("jarvis.security.paths.os.path.expanduser", side_effect=lambda p: p.replace("~", str(tmp_path))):
        from jarvis.tools.file_ops import write_file, read_file
        (tmp_path / "JarvisTestFree").mkdir()
        write_file("~/JarvisTestFree/n.txt", "hi")
        assert read_file("~/JarvisTestFree/n.txt") == "hi"
