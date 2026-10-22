# tests/test_decision_judge.py
import pytest

from jarvis.decision import rules_fallback
from jarvis.decision.decide import DecisionMaker
from jarvis.decision.jev_client import JevClient
from jarvis.judge import Judge
from jarvis.security.audit import AuditLog
from jarvis.security.gatekeeper import Gatekeeper
from jarvis.security.policy import DEFAULT_UNTRUSTED_TOOLS, Policy, Tier


# --- decision maker (rules fallback) ---

def test_vague_destructive_request_asks_first():
    d = rules_fallback.decide("delete the old stuff")
    assert d.route == "ask" and "Which files" in d.clarify_question


def test_vague_request_right_after_another_turn_is_not_asked():
    assert rules_fallback.decide("delete them", recent_turn=True).route == "claude"


def test_concrete_destructive_request_needs_plan_not_clarification():
    d = rules_fallback.decide("delete everything in my Downloads folder")
    assert d.route == "claude" and d.unclear == 0
    assert d.expected_risk >= 7 and d.needs_plan >= 0.5
    assert "file" in d.intents


def test_simple_request_is_low_risk_no_plan():
    d = rules_fallback.decide("open notepad")
    assert d.expected_risk == 2 and d.needs_plan < 0.5 and d.route == "claude"


def test_multi_step_request_needs_plan():
    d = rules_fallback.decide("find my old invoices, rename them by date and then move them to Documents")
    assert d.complexity >= 6 and d.needs_plan >= 0.5


def test_outward_actions_raise_risk():
    assert rules_fallback.decide("send an email to Ravi").expected_risk >= 6


def test_tool_trimming():
    d = rules_fallback.decide("list the files on my desktop")
    names = d.tool_names()
    assert names is not None and "list_files" in names and "web_search" not in names
    assert rules_fallback.decide("hmm").tool_names() is None   # unknown intent = all tools


def test_local_for_simple_routes_easy_requests_locally():
    d = rules_fallback.decide("what is a black hole", local_for_simple=True)
    assert d.route == "local"
    assert rules_fallback.decide("delete my notes.txt", local_for_simple=True).route == "claude"


def test_decision_maker_uses_rules_when_jev_unavailable():
    dm = DecisionMaker(provider="jev", jev=JevClient())
    assert dm.decide("open notepad").source == "rules"


def test_decision_maker_tracks_recent_turns():
    now = [0.0]
    dm = DecisionMaker(clock=lambda: now[0], recent_turn_s=300)
    assert dm.decide("delete it").route == "ask"      # first turn: nothing to refer to
    now[0] += 10
    assert dm.decide("delete it").route == "claude"   # follow-up: "it" can refer to the last turn


# --- judge-first ---

@pytest.fixture
def env(tmp_path):
    free, other, blocked = tmp_path / "free", tmp_path / "other", tmp_path / "blocked"
    for d in (free, other, blocked):
        d.mkdir()
    policy = Policy(free_paths=[str(free)], blocked_paths=[str(blocked)],
                    untrusted_tools=set(DEFAULT_UNTRUSTED_TOOLS), audit_db=str(tmp_path / "a.db"))
    asked = []
    gk = Gatekeeper(policy_loader=lambda: policy, confirmer=lambda r: asked.append(("single", r.tool)) or False,
                    audit=AuditLog(policy.audit_db))
    answer = {"choice": "allow"}

    def ask_choice(req, steps, alt):
        asked.append(("plan", req.tier, list(steps), alt))
        return answer["choice"]
    return gk, Judge(gk, ask_choice), free, other, blocked, asked, answer


def _dispatch(calls):
    return lambda tool, args: calls.append(tool) or f"ran {tool}"


def test_plan_with_never_step_is_rejected_before_anything_runs(env):
    gk, judge, free, _, blocked, asked, _ = env
    turn = gk.new_turn("voice")
    out = judge.propose(turn, {"intent": "x", "steps": [
        {"tool": "list_files", "args": {"path": str(free)}},
        {"tool": "read_file", "args": {"path": str(blocked / "s.txt")}}]})
    assert out.startswith("PLAN REJECTED: step 2") and asked == [] and not turn.plan_accepted


def test_all_free_plan_is_approved_without_asking(env):
    gk, judge, free, *_ = env
    asked = env[5]
    turn = gk.new_turn("voice")
    out = judge.propose(turn, {"intent": "x", "steps": [{"tool": "list_files", "args": {"path": str(free)}}]})
    assert out.startswith("PLAN APPROVED") and turn.plan_accepted and asked == []


def test_approved_plan_runs_its_steps_without_second_ask(env):
    gk, judge, free, other, _, asked, _ = env
    (free / "a.txt").write_text("old")
    turn = gk.new_turn("voice")
    turn.needs_plan = True
    steps = [{"tool": "write_file", "args": {"path": str(free / "a.txt"), "content": "new"}},
             {"tool": "read_file", "args": {"path": str(other / "n.txt")}}]
    out = judge.propose(turn, {"intent": "update notes", "steps": steps, "alternative": "read only"})
    assert out.startswith("PLAN APPROVED by the owner")
    kind, tier, lines, alt = asked[0]
    assert kind == "plan" and tier == Tier.STRONG and len(lines) == 2 and alt == "read only"
    calls = []
    assert gk.run(turn, "write_file", steps[0]["args"], _dispatch(calls)) == "ran write_file"
    assert gk.run(turn, "read_file", steps[1]["args"], _dispatch(calls)) == "ran read_file"
    assert len(asked) == 1  # no single-action prompts


def test_step_not_in_plan_still_asks(env):
    gk, judge, free, other, _, asked, _ = env
    turn = gk.new_turn("voice")
    judge.propose(turn, {"intent": "x", "steps": [{"tool": "read_file", "args": {"path": str(other / "a.txt")}}]})
    out = gk.run(turn, "read_file", {"path": str(other / "b.txt")}, _dispatch([]))
    assert out.startswith("BLOCKED") and asked[-1] == ("single", "read_file")


def test_declined_or_alternative_plan(env):
    gk, judge, _, other, _, _, answer = env
    plan = {"intent": "x", "steps": [{"tool": "read_file", "args": {"path": str(other / "a.txt")}}],
            "alternative": "skip it"}
    answer["choice"] = "deny"
    assert judge.propose(gk.new_turn("voice"), plan).startswith("PLAN DECLINED")
    answer["choice"] = "alternative"
    assert "safer option: skip it" in judge.propose(gk.new_turn("voice"), plan)


def test_acting_tools_wait_for_a_plan_without_counting_as_denial(env):
    gk, judge, free, *_ = env
    turn = gk.new_turn("voice")
    turn.needs_plan = True
    calls = []
    for _ in range(3):
        out = gk.run(turn, "write_file", {"path": str(free / "x.txt"), "content": "hi"}, _dispatch(calls))
        assert out.startswith("WAIT")
    assert turn.denials == 0 and not turn.stopped and calls == []
    assert gk.run(turn, "list_files", {"path": str(free)}, _dispatch(calls)) == "ran list_files"  # reading is fine
    judge.propose(turn, {"intent": "x", "steps": [{"tool": "write_file", "args": {"path": str(free / "x.txt"), "content": "hi"}}]})
    assert gk.run(turn, "write_file", {"path": str(free / "x.txt"), "content": "hi"}, _dispatch(calls)) == "ran write_file"


def test_empty_or_huge_plans_rejected(env):
    gk, judge, *_ = env
    assert judge.propose(gk.new_turn("voice"), {"intent": "x", "steps": []}).startswith("PLAN REJECTED")
    many = [{"tool": "get_volume", "args": {}}] * 26
    assert "too many" in judge.propose(gk.new_turn("voice"), {"intent": "x", "steps": many})
