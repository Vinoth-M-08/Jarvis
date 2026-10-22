"""Judge first: the brain proposes a plan, the gatekeeper checks every step before anything runs,
and you approve it once (PLAN.md §4.7; idea from the jev-chat app).

- Any Tier 3 step rejects the whole plan — nothing runs.
- All steps Tier 0 → approved without asking.
- Otherwise one plan card in the HUD (+ one spoken sentence): Allow / safer option / Deny.
- Approved Tier 1–2 steps then run without a second ask, for their exact targets only;
  steps that weren't in the plan still ask as usual.
"""
from __future__ import annotations
from typing import Callable

from jarvis.security.confirm import describe
from jarvis.security.gatekeeper import ConfirmRequest, Gatekeeper, TurnContext, step_key
from jarvis.security.policy import Tier

MAX_STEPS = 25

PROPOSE_PLAN_SCHEMA = {"type": "function", "function": {
    "name": "propose_plan",
    "description": (
        "Before acting on a request with several steps or one that changes files, list every step you "
        "will take (tool + exact args). The owner approves the plan once; approved steps then run without "
        "asking again. Steps you didn't list need approval separately."),
    "parameters": {"type": "object", "properties": {
        "intent": {"type": "string", "description": "One sentence: what the owner wants"},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "tool": {"type": "string"},
            "args": {"type": "object"},
            "why": {"type": "string"}}, "required": ["tool", "args"]}},
        "alternative": {"type": "string", "description": "Optional safer variant, e.g. 'move only, don't delete'"}},
        "required": ["intent", "steps"]}}}


class Judge:
    def __init__(self, gatekeeper: Gatekeeper,
                 ask_choice: Callable[[ConfirmRequest, list[str], str | None], str]):
        self.gatekeeper = gatekeeper
        self._ask_choice = ask_choice

    def propose(self, turn: TurnContext, args: dict) -> str:
        steps = args.get("steps") or []
        intent = str(args.get("intent", "")).strip()
        alternative = (str(args.get("alternative") or "").strip()) or None
        if not isinstance(steps, list) or not steps:
            return "PLAN REJECTED: no steps listed. List every step as {tool, args}."
        if len(steps) > MAX_STEPS:
            return f"PLAN REJECTED: {len(steps)} steps is too many (max {MAX_STEPS}). Split the task."

        checked: list[tuple[str, dict, Tier, str]] = []
        for i, step in enumerate(steps, 1):
            tool = str((step or {}).get("tool", ""))
            step_args = (step or {}).get("args") or {}
            if not isinstance(step_args, dict):
                return f"PLAN REJECTED: step {i} has invalid args."
            tier, reason, _ = self.gatekeeper.effective_tier(turn, tool, step_args)
            if tier == Tier.NEVER:
                self.gatekeeper.audit.record(turn.id, turn.source, "propose_plan", args, 3, "deny", reason)
                return (f"PLAN REJECTED: step {i} ({describe(tool, step_args)}) is never allowed: {reason}. "
                        "Don't do it or work around it; tell the owner in one sentence.")
            checked.append((tool, step_args, tier, reason))

        top = max(tier for _, _, tier, _ in checked)
        if top == Tier.FREE:
            turn.plan_accepted = True
            self.gatekeeper.audit.record(turn.id, turn.source, "propose_plan", args, 0, "allow", "all steps free")
            return "PLAN APPROVED (no approval needed). Go ahead with exactly these steps."

        lines = [f"{describe(t, a)}" + ("" if tier == Tier.FREE else f"  [tier {int(tier)}]")
                 for t, a, tier, _ in checked]
        needs = sum(1 for _t, _a, tier, _r in checked if tier > Tier.FREE)
        req = ConfirmRequest(turn, "plan", {"intent": intent or "the requested task"}, top,
                             f"{needs} of {len(checked)} steps need your OK")
        choice = self._ask_choice(req, lines, alternative)

        if choice == "allow":
            turn.plan_accepted = True
            turn.approved_steps.update(step_key(t, a) for t, a, tier, _ in checked if tier > Tier.FREE)
            self.gatekeeper.audit.record(turn.id, turn.source, "propose_plan", args, int(top), "allow", "owner approved")
            return "PLAN APPROVED by the owner. Do exactly these steps; anything else needs approval."
        if choice == "alternative" and alternative:
            self.gatekeeper.audit.record(turn.id, turn.source, "propose_plan", args, int(top), "deny", "owner chose alternative")
            return f"The owner chose the safer option: {alternative}. Propose a new plan for that."
        self.gatekeeper.audit.record(turn.id, turn.source, "propose_plan", args, int(top), "deny", "owner declined")
        return "PLAN DECLINED by the owner. Don't do it; ask what they'd like instead."
