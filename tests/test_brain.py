# tests/test_brain.py
import asyncio
import os
import threading

import pytest

from jarvis.brain.claude_agent import BrainAborted, ClaudeBrain, TOOL_PREFIX, fence_hook, jarvis_tool_name
from jarvis.brain.claude_env import child_env, scrub_process_env


# --- env stripping ---

def test_child_env_strips_billing_and_backend_vars():
    base = {
        "ANTHROPIC_API_KEY": "x", "anthropic_auth_token": "y", "ANTHROPIC_BASE_URL": "z",
        "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDECODE": "1",
        "CLAUDE_CODE_GIT_BASH_PATH": r"C:\Git\bin\bash.exe", "PATH": "p",
    }
    env = child_env(base)
    assert env == {"CLAUDE_CODE_GIT_BASH_PATH": r"C:\Git\bin\bash.exe", "PATH": "p"}


def test_scrub_process_env_returns_names_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-value")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", "keep-me")
    removed = scrub_process_env()
    assert "ANTHROPIC_API_KEY" in removed and "CLAUDE_CODE_USE_VERTEX" in removed
    assert "secret-value" not in " ".join(removed)
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["CLAUDE_CODE_GIT_BASH_PATH"] == "keep-me"


# --- tool fence ---

def test_jarvis_tool_name():
    assert jarvis_tool_name(TOOL_PREFIX + "open_app") == "open_app"
    assert jarvis_tool_name("Bash") is None
    assert jarvis_tool_name("mcp__other__open_app") is None


def test_fence_hook_allows_only_jarvis_tools():
    assert asyncio.run(fence_hook({"tool_name": TOOL_PREFIX + "list_files"}, "id1", None)) == {}
    for name in ("Bash", "Write", "Read", "mcp__other__x", "SomeFutureTool"):
        out = asyncio.run(fence_hook({"tool_name": name}, "id2", None))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", name


def test_fence_permission_allows_only_jarvis_tools():
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
    from jarvis.brain.claude_agent import fence_permission
    assert isinstance(asyncio.run(fence_permission(TOOL_PREFIX + "open_app", {"name": "x"}, None)), PermissionResultAllow)
    assert isinstance(asyncio.run(fence_permission("Bash", {"command": "dir"}, None)), PermissionResultDeny)


# --- tool handlers go through run_tool (the gatekeeper) ---

def _brain(run_tool=None, **kw):
    return ClaudeBrain(tool_schemas=[], run_tool=run_tool or (lambda t, n, a: "ok"),
                       system_prompt=lambda: "sys", **kw)


def test_handler_routes_through_run_tool_with_current_turn():
    calls, seen, results = [], [], []
    brain = _brain(run_tool=lambda turn, name, args: calls.append((turn, name, args)) or "ran it",
                   on_tool=lambda n, a: seen.append(n), on_tool_result=lambda n, r: results.append(r))
    brain._turn = "TURN"
    out = asyncio.run(brain._make_handler("open_app")({"name": "notepad"}))
    assert calls == [("TURN", "open_app", {"name": "notepad"})]
    assert out == {"content": [{"type": "text", "text": "ran it"}]}
    assert seen == ["open_app"] and results == ["ran it"]


def test_sdk_tools_cover_every_router_tool():
    pytest.importorskip("claude_agent_sdk")
    from jarvis.tools.router import TOOL_SCHEMAS
    brain = ClaudeBrain(tool_schemas=TOOL_SCHEMAS, run_tool=lambda t, n, a: "ok", system_prompt=lambda: "s")
    assert len(brain._sdk_tools()) == len(TOOL_SCHEMAS)


# --- blocking ask() ---

def test_ask_returns_answer_and_clears_turn():
    brain = _brain()

    async def fake_ask(prompt, **kwargs):
        assert brain._turn == "TURN"
        return f"echo: {prompt}"
    brain._ask = fake_ask
    assert brain.ask("hi", "TURN") == "echo: hi"
    assert brain._turn is None


def test_ask_aborts_on_escape():
    brain = _brain()

    async def slow_ask(prompt, **kwargs):
        await asyncio.sleep(0.6)
        return "too late"
    brain._ask = slow_ask
    abort = threading.Event()
    threading.Timer(0.1, abort.set).start()
    with pytest.raises(BrainAborted):
        brain.ask("hi", "TURN", abort)
    assert brain._turn is None


def test_ask_propagates_errors_for_local_fallback():
    brain = _brain()

    async def failing_ask(prompt, **kwargs):
        raise RuntimeError("Claude run failed (error_during_execution): not logged in")
    brain._ask = failing_ask
    with pytest.raises(RuntimeError, match="not logged in"):
        brain.ask("hi", "TURN")


def test_failed_run_reports_claude_codes_reason():
    """Claude Code puts the reason in `result` ("Failed to authenticate: ...") with is_error set."""
    from claude_agent_sdk import ResultMessage

    class FakeClient:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0, is_error=True,
                                num_turns=1, session_id="s",
                                result="Failed to authenticate: OAuth session expired and could not be refreshed")

    brain = ClaudeBrain(tool_schemas=[], run_tool=lambda *a: "", system_prompt=lambda: "")

    async def fake_connect():
        return FakeClient()
    brain._connect = fake_connect

    with pytest.raises(RuntimeError, match="OAuth session expired"):
        asyncio.run(brain._ask("hi"))


def test_options_load_tools_up_front_and_stream_words():
    brain = ClaudeBrain(tool_schemas=[], run_tool=lambda *a: "", system_prompt=lambda: "")
    opts = brain._options()
    assert opts.env.get("ENABLE_TOOL_SEARCH") == "false"   # no ToolSearch round before each tool
    assert opts.include_partial_messages is True


def test_text_delta_reads_only_text():
    from jarvis.brain.claude_agent import text_delta
    assert text_delta({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}) == "Hi"
    assert text_delta({"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}}) == ""
    assert text_delta({"type": "message_start"}) == "" and text_delta(None) == ""


def _fake_client(*messages):
    class FakeClient:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            for m in messages:
                yield m
    return FakeClient()


def test_streamed_words_reach_on_text_and_warm_up_is_not_counted():
    from claude_agent_sdk import ResultMessage, StreamEvent
    ev = lambda t: StreamEvent(uuid="u", session_id="s", parent_tool_use_id=None,
                               event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}})
    done = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s", result="Opening Spotify.")
    usage = []
    brain = ClaudeBrain(tool_schemas=[], run_tool=lambda *a: "", system_prompt=lambda: "", on_usage=usage.append)

    async def connect():
        return _fake_client(ev("Opening "), ev("Spotify."), done)
    brain._connect = connect

    words = []
    assert asyncio.run(brain._ask("play music", on_text=words.append)) == "Opening Spotify."
    assert words == ["Opening ", "Spotify."] and len(usage) == 1

    brain.warm_up()                       # start-up check: not reported as a request
    assert len(usage) == 1
