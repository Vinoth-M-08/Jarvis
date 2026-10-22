# tests/test_windows_mcp.py
from types import SimpleNamespace

import pytest

from jarvis.security.audit import AuditLog
from jarvis.security.gatekeeper import Gatekeeper, classify
from jarvis.security.policy import DEFAULT_UNTRUSTED_TOOLS, Policy, Tier
from jarvis.tools.windows_mcp import NEVER_LOAD, WindowsMCP, result_text, to_function_schema


# --- client helpers ---

def test_schema_conversion_prefixes_and_drops_ctx():
    s = to_function_schema("Click", "Click an element.", {
        "type": "object",
        "properties": {"loc": {"type": "array"}, "label": {"type": "integer"}, "ctx": {"type": "object"}},
        "required": ["ctx"],
    })
    fn = s["function"]
    assert fn["name"] == "win_click"
    assert fn["description"].startswith("[Windows desktop] Click")
    assert "ctx" not in fn["parameters"]["properties"] and fn["parameters"]["required"] == []


def test_result_text_flattens_content():
    ok = SimpleNamespace(content=[SimpleNamespace(type="text", text="Clicked."),
                                  SimpleNamespace(type="image", data="...")], isError=False)
    assert result_text(ok, "win_click") == "Clicked.\n[image omitted]"
    err = SimpleNamespace(content=[SimpleNamespace(type="text", text="no element 9")], isError=True)
    assert result_text(err, "win_click") == "Tool 'win_click' failed: no element 9"


def test_dangerous_tools_are_never_requested():
    client = WindowsMCP(tools=["Click", "Registry", "FileSystem", "Process", "Snapshot"])
    requested = client.args[client.args.index("--tools") + 1].split(",")
    assert requested == ["Click", "Snapshot"]
    assert {"Registry", "FileSystem", "Process"} <= NEVER_LOAD


def test_calls_fail_cleanly_when_not_running():
    client = WindowsMCP()
    assert not client.running
    assert client.call("win_click", {}).startswith("Unknown tool")
    client._names = {"win_click": "Click"}
    assert "not running" in client.call("win_click", {"label": 3})


# --- tiers for desktop tools ---

@pytest.fixture
def policy(tmp_path):
    free = tmp_path / "free"
    free.mkdir()
    return Policy(free_paths=[str(free)], blocked_paths=[r"C:\Windows"],
                  untrusted_tools=set(DEFAULT_UNTRUSTED_TOOLS), audit_db=str(tmp_path / "a.db"))


def test_windows_tool_tiers(policy):
    assert classify("win_snapshot", {}, policy)[0] == Tier.FREE
    assert classify("win_click", {"label": 4}, policy)[0] == Tier.FREE
    assert classify("win_app", {"mode": "switch", "name": "Notepad"}, policy)[0] == Tier.FREE
    assert classify("win_app", {"mode": "launch", "name": "Calculator"}, policy)[0] == Tier.FREE
    assert classify("win_app", {"mode": "launch_executable", "executable": r"C:\x\setup.exe"}, policy)[0] == Tier.CONFIRM
    assert classify("win_app", {"mode": "hack"}, policy)[0] == Tier.NEVER
    assert classify("win_shortcut", {"shortcut": "ctrl+c"}, policy)[0] == Tier.FREE
    assert classify("win_shortcut", {"shortcut": "Alt+F4"}, policy)[0] == Tier.CONFIRM
    for name in ("win_registry", "win_filesystem", "win_process", "win_anything"):
        assert classify(name, {}, policy)[0] == Tier.NEVER


def test_powershell_goes_through_shell_checker(policy):
    free = policy.free_paths[0]
    assert classify("win_powershell", {"command": f'Get-ChildItem "{free}"'}, policy)[0] == Tier.FREE
    assert classify("win_powershell", {"command": f'Remove-Item "{free}\\x.txt"'}, policy)[0] == Tier.STRONG
    assert classify("win_powershell", {"command": "powershell -EncodedCommand AAAA"}, policy)[0] == Tier.NEVER


# --- one approval for screen control per task ---

def _gk(policy, answers):
    asked = []

    def confirmer(req):
        asked.append((req.tool, req.reason))
        return answers.pop(0) if answers else False
    return Gatekeeper(policy_loader=lambda: policy, confirmer=confirmer, audit=AuditLog(policy.audit_db)), asked


def test_screen_control_is_approved_once_per_task(policy):
    gk, asked = _gk(policy, [True])
    turn = gk.new_turn("voice")
    run = lambda tool, args: gk.run(turn, tool, args, lambda t, a: f"ran {t}")
    assert run("win_snapshot", {}) == "ran win_snapshot"
    assert run("win_click", {"label": 3}) == "ran win_click"
    assert run("win_type", {"text": "hello", "label": 5}) == "ran win_type"
    assert run("win_click", {"label": 7}) == "ran win_click"
    assert len(asked) == 1 and "click and type" in asked[0][1]


def test_screen_control_declined_blocks_ui_actions(policy):
    gk, asked = _gk(policy, [False])
    turn = gk.new_turn("voice")
    gk.run(turn, "win_snapshot", {}, lambda t, a: "ui")
    assert gk.run(turn, "win_click", {"label": 3}, lambda t, a: "ran").startswith("BLOCKED")


def test_screen_approval_does_not_cover_other_tools(policy):
    gk, asked = _gk(policy, [True, False])
    turn = gk.new_turn("voice")
    gk.run(turn, "win_snapshot", {}, lambda t, a: "ui")
    gk.run(turn, "win_click", {"label": 3}, lambda t, a: "ran")
    out = gk.run(turn, "win_powershell", {"command": "Get-Process"}, lambda t, a: "ran")
    assert out.startswith("BLOCKED") and len(asked) == 2   # PowerShell asked separately


def test_screen_approval_resets_next_task(policy):
    gk, asked = _gk(policy, [True, True])
    t1 = gk.new_turn("voice")
    gk.run(t1, "win_snapshot", {}, lambda t, a: "ui")
    gk.run(t1, "win_click", {"label": 1}, lambda t, a: "ran")
    t2 = gk.new_turn("voice")
    gk.run(t2, "win_snapshot", {}, lambda t, a: "ui")
    gk.run(t2, "win_click", {"label": 1}, lambda t, a: "ran")
    assert len(asked) == 2
