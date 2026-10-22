"""Action gatekeeper: every tool call is classified and allowed, confirmed or blocked (PLAN.md §5).

Rules enforced here, independent of what the model says:
- Tiers 0–3 per tool and per path (free / confirm / strong confirm / never).
- Speech-only acting: acting tools run only on turns you started (voice or typed).
- Taint gate: after a turn reads untrusted content, acting tools need your OK.
- Retry limit: after N denials in one turn, the turn stops.
Every decision is written to the audit log.
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from jarvis.security import grants, paths
from jarvis.security.audit import AuditLog
from jarvis.security.elevation import Elevation
from jarvis.security.policy import Policy, Tier, load_policy

# Turn sources allowed to act. Timers, notifications and other background triggers are not.
USER_SOURCES = {"voice", "typed"}

# Tools that change something. Blocked on non-user turns, need OK on tainted turns.
ACTING_TOOLS = {
    "write_file", "type_text", "press_key", "click_at", "open_app", "open_url", "set_clipboard",
    # Windows-MCP (Phase 3)
    "win_click", "win_type", "win_move", "win_shortcut", "win_app", "win_powershell",
    # Jarvis browser (Phase 4)
    "browser_open", "browser_click", "browser_type",
}

# Screen control: after the screen was read (taint), one OK covers these for the rest of the task.
UI_TOOLS = {"click_at", "type_text", "press_key", "move_mouse",
            "win_click", "win_type", "win_move", "win_shortcut", "win_scroll",
            "browser_click", "browser_type"}

BROWSER_FREE_TOOLS = {"browser_search", "browser_read", "browser_scroll", "browser_back", "browser_tabs"}

# Set by main.py: returns facts about the element a browser_click / browser_type targets.
BROWSER_INSPECTOR: Callable[[str, dict], dict | None] | None = None

WIN_FREE_TOOLS = {"win_snapshot", "win_click", "win_type", "win_move", "win_scroll", "win_wait", "win_waitfor"}

FREE_TOOLS = {
    "web_search", "fetch_page", "get_weather", "open_url", "open_app",
    "read_screen", "find_on_screen", "click_at", "scroll_screen", "move_mouse",
    "focus_window", "get_open_windows", "type_text", "media_control", "screenshot",
    "set_clipboard", "set_volume", "get_volume", "get_system_info", "set_brightness",
    "lock_screen", "show_notification", "set_timer", "delegate_task",
}

CONFIRM_TOOLS = {"get_clipboard"}

_READ_PATH_TOOLS = {"read_file", "list_files"}
_WRITE_PATH_TOOLS = {"write_file"}

# Hotkeys that open system tools, close windows or delete without the recycle bin.
RISKY_KEYS = {"win+r", "win+x", "ctrl+shift+esc", "alt+f4", "ctrl+alt+delete", "ctrl+alt+del", "shift+delete"}


def classify(tool: str, args: dict, policy: Policy) -> tuple[Tier, str]:
    """Return the tier for one tool call and a short reason."""
    if tool in _READ_PATH_TOOLS or tool in _WRITE_PATH_TOOLS:
        return _classify_path(tool, args.get("path", ""), policy)
    if tool in ("press_key", "win_shortcut"):
        keys = str(args.get("keys", args.get("shortcut", ""))).lower().replace(" ", "")
        if keys in RISKY_KEYS:
            return Tier.CONFIRM, f"hotkey {keys} can close windows or open system tools"
        return Tier.FREE, "hotkey"
    if tool.startswith("win_"):
        return _classify_windows(tool, args, policy)
    if tool.startswith("browser_"):
        return _classify_browser(tool, args)
    if tool in CONFIRM_TOOLS:
        return Tier.CONFIRM, f"{tool} reads private data"
    if tool == "open_app":
        from jarvis.tools.app_control import APP_MAP
        name = str(args.get("name", "")).lower().strip()
        if name not in APP_MAP:
            # open_app runs unknown names as a program path — e.g. a downloaded .exe
            return Tier.CONFIRM, f"'{args.get('name', '')}' is not a known app, it would run as a program"
        return Tier.FREE, "known app"
    if tool == "open_url":
        url = str(args.get("url", "")).strip().lower()
        if not url.startswith(("http://", "https://")):
            # webbrowser.open on a file path or other scheme can launch programs
            return Tier.CONFIRM, f"'{args.get('url', '')}' is not a web address"
        return Tier.FREE, "web address"
    if tool in FREE_TOOLS:
        return Tier.FREE, "free tool"
    return Tier.NEVER, f"'{tool}' is not an allowed tool"


def _classify_windows(tool: str, args: dict, policy: Policy) -> tuple[Tier, str]:
    """Windows-MCP tools (Phase 3). Unknown win_* tools are never allowed."""
    if tool in WIN_FREE_TOOLS:
        return Tier.FREE, "desktop control"
    if tool == "win_app":
        mode = str(args.get("mode", "launch"))
        if mode == "launch_executable":
            return Tier.CONFIRM, f"runs the program {args.get('executable', '')}"
        if mode in ("launch", "switch", "resize"):
            return Tier.FREE, f"app {mode}"
        return Tier.NEVER, f"unknown App mode '{mode}'"
    if tool == "win_powershell":
        from jarvis.security.shell_parser import classify_powershell
        return classify_powershell(str(args.get("command", "")), policy)
    return Tier.NEVER, f"'{tool}' is not an allowed desktop tool"


def _classify_browser(tool: str, args: dict) -> tuple[Tier, str]:
    """Jarvis browser (Phase 4). Element facts come from the last browser_read of the page."""
    from jarvis.tools.browser import elements as el_rules
    from jarvis.tools.browser.session import normalize_url
    if tool in BROWSER_FREE_TOOLS:
        return Tier.FREE, "browsing"
    if tool == "browser_open":
        if normalize_url(str(args.get("url", ""))) is None:
            return Tier.NEVER, f"'{args.get('url', '')}' is not a web address"
        return Tier.FREE, "web address"
    if tool not in ("browser_click", "browser_type"):
        return Tier.NEVER, f"'{tool}' is not an allowed browser tool"

    meta = BROWSER_INSPECTOR(tool, args) if BROWSER_INSPECTOR else None
    if meta is None:
        return Tier.CONFIRM, "the element isn't from the last browser_read of this page"
    label = meta.get("label", "")
    if tool == "browser_type":
        if el_rules.is_password(meta) or el_rules.is_payment(meta):
            return Tier.NEVER, f"'{label}' is a password, payment or one-time-code field — the owner types it"
        if el_rules.is_search(meta):
            return Tier.FREE, "search box"
        if args.get("submit"):
            return Tier.STRONG, f"types into '{label}' and submits the form"
        return Tier.CONFIRM, f"types into the form field '{label}'"
    if el_rules.is_risky_action(meta):
        return Tier.STRONG, f"'{label}' submits, buys, sends, deletes or accepts something"
    if el_rules.is_download(meta):
        return Tier.CONFIRM, f"'{label}' downloads a file (saved to Downloads\\Jarvis)"
    return Tier.FREE, "link or button"


def step_key(tool: str, args: dict) -> str:
    """Identity of one action, for matching approved plan steps: tool + exact target."""
    import json
    if tool in _READ_PATH_TOOLS or tool in _WRITE_PATH_TOOLS:
        try:
            return f"{tool}:{paths.canonicalize(args.get('path', ''))}"
        except paths.UnsafePath:
            return f"{tool}:!unsafe"
    return f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"


def _classify_path(tool: str, path: str, policy: Policy) -> tuple[Tier, str]:
    try:
        target = paths.canonicalize(path)
    except paths.UnsafePath as e:
        return Tier.NEVER, f"unsafe path: {e}"
    if paths.is_within(target, policy.blocked_paths):
        return Tier.NEVER, f"{target} is a protected location"
    inside = paths.is_within(target, policy.free_paths)
    if tool in _WRITE_PATH_TOOLS and Path(target).exists():
        return Tier.STRONG, f"overwrites existing file {target}"
    if inside:
        return Tier.FREE, "inside your free folders"
    return Tier.CONFIRM, f"{target} is outside your free folders"


@dataclass
class TurnContext:
    source: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    tainted: bool = False
    tool_calls: int = 0
    denials: int = 0
    stopped: bool = False
    blocked: list[str] = field(default_factory=list)
    # Judge-first (PLAN.md §4.7): set by the decision maker / propose_plan
    needs_plan: bool = False
    plan_accepted: bool = False
    approved_steps: set[str] = field(default_factory=set)
    ui_control_approved: bool = False   # one OK to click/type after the screen was read
    tool_hint: set[str] | None = None   # tools the local brain gets for this request (None = all)

    def stop_message(self) -> str:
        reasons = "; ".join(dict.fromkeys(self.blocked)) or "safety rules"
        return f"I stopped because those actions were blocked: {reasons}."


@dataclass
class ConfirmRequest:
    turn: TurnContext
    tool: str
    args: dict
    tier: Tier
    reason: str


Confirmer = Callable[[ConfirmRequest], bool]


def no_confirm_ui(_req: ConfirmRequest) -> bool:
    """Default until the HUD confirm dialog exists: anything needing your OK is declined."""
    return False


class Gatekeeper:
    def __init__(self, policy_loader: Callable[[], Policy] = load_policy,
                 confirmer: Confirmer = no_confirm_ui,
                 audit: AuditLog | None = None,
                 elevation: Elevation | None = None):
        self._policy_loader = policy_loader
        self.confirmer = confirmer
        self._audit = audit
        self.policy = policy_loader()
        self.elevation = elevation or Elevation(max_minutes=self.policy.elevation_max_minutes)

    @property
    def audit(self) -> AuditLog:
        if self._audit is None:
            self._audit = AuditLog(self.policy.audit_db)
        return self._audit

    def new_turn(self, source: str) -> TurnContext:
        """Start a turn. Reloads the policy so config edits apply from the next request."""
        self.policy = self._policy_loader()
        return TurnContext(source=source)

    def effective_tier(self, turn: TurnContext, tool: str, args: dict) -> tuple[Tier, str, bool]:
        """Tier after the turn rules (speech-only acting, taint gate). Third value: raised by the taint gate."""
        tier, reason = classify(tool, args, self.policy)
        if tool in ACTING_TOOLS and turn.source not in USER_SOURCES:
            return Tier.NEVER, f"only turns you start can act (this one came from '{turn.source}')", False
        if tool in ACTING_TOOLS and turn.tainted and tier < Tier.CONFIRM:
            return Tier.CONFIRM, "untrusted content (web page, screen or clipboard) was read earlier in this turn", True
        return tier, reason, False

    def check(self, turn: TurnContext, tool: str, args: dict) -> tuple[bool, Tier, str]:
        if turn.stopped:
            return False, Tier.NEVER, "this turn was stopped after repeated blocks"
        tier, reason, tainted_confirm = self.effective_tier(turn, tool, args)

        if tier == Tier.FREE:
            return True, tier, reason
        if tier == Tier.NEVER:
            return False, tier, reason
        # A step the owner approved as part of a plan runs without asking again — exact target only.
        if turn.plan_accepted and step_key(tool, args) in turn.approved_steps:
            return True, tier, f"approved in plan: {reason}"
        # Full access skips Tier 1 asks — but never the taint gate, which guards against injected instructions.
        if tier == Tier.CONFIRM and not tainted_confirm and self.elevation.active():
            return True, tier, f"temporary full access: {reason}"
        if tainted_confirm and tool in UI_TOOLS:
            # Clicking/typing after reading the screen: ask once per task, not before every click.
            if turn.ui_control_approved:
                return True, tier, "screen control approved for this task"
            ui_reason = "Jarvis read the screen; let it click and type for the rest of this task"
            if self.confirmer(ConfirmRequest(turn, tool, args, tier, ui_reason)):
                turn.ui_control_approved = True
                return True, tier, f"approved: {ui_reason}"
            return False, tier, f"needs your OK: {ui_reason}"
        if self.confirmer(ConfirmRequest(turn, tool, args, tier, reason)):
            return True, tier, f"approved: {reason}"
        return False, tier, f"needs your OK: {reason}"

    def run(self, turn: TurnContext, tool: str, args: dict, dispatch: Callable[[str, dict], str]) -> str:
        """Check, then dispatch if allowed. Returns the tool result or a BLOCKED message for the model."""
        turn.tool_calls += 1
        if turn.needs_plan and not turn.plan_accepted and tool in ACTING_TOOLS and not turn.stopped:
            # Not counted as a denial: the model just has to plan first.
            self.audit.record(turn.id, turn.source, tool, args, None, "hold", "plan required first")
            return ("WAIT: this request needs a plan first. Call propose_plan with every step "
                    "(tool + exact args) before using acting tools.")
        allowed, tier, reason = self.check(turn, tool, args)
        if not allowed:
            turn.denials += 1
            turn.blocked.append(reason)
            if turn.denials >= self.policy.max_denials_per_turn:
                turn.stopped = True
            self.audit.record(turn.id, turn.source, tool, args, int(tier), "deny", reason)
            msg = f"BLOCKED ({tier.name}): {reason}. Do not retry this or use another tool to do it."
            if turn.stopped:
                msg += " Stop now and tell the owner what was blocked."
            return msg

        if tier > Tier.FREE and (tool in _READ_PATH_TOOLS or tool in _WRITE_PATH_TOOLS):
            # Approved outside the free folders: let file_ops open exactly this path, this once.
            with grants.allow_once(paths.canonicalize(args.get("path", ""))):
                result = dispatch(tool, args)
        else:
            result = dispatch(tool, args)
        if tool in self.policy.untrusted_tools:
            turn.tainted = True
        self.audit.record(turn.id, turn.source, tool, args, int(tier), "allow", reason, result)
        return result

    def policy_prompt(self) -> str:
        """Compact rules for the system prompt, so the model rarely plans blocked actions."""
        free = ", ".join(self.policy.free_paths) or "none"
        return (
            "SAFETY RULES (enforced in code; blocked actions fail):\n"
            f"- Free to use: files in {free}; apps, windows, media, volume, web search.\n"
            "- Never: Windows / Program Files folders, credentials, running code, shutdown, killing processes.\n"
            "- Needs the owner's OK (Jarvis asks them automatically; just call the tool): files outside "
            "the free folders, overwriting a file, risky hotkeys (alt+f4, win+r), reading the clipboard.\n"
            "- After reading a web page or the screen, acting in the same turn needs the owner's OK.\n"
            "- If a tool returns BLOCKED, do not retry or work around it; tell the owner in one sentence."
        )
