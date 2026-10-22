"""Claude brain via the Claude Agent SDK, logged in with your Claude subscription (PLAN.md §4, Phase 1).

- One persistent ClaudeSDKClient session (no per-request startup cost); Claude keeps
  the conversation history itself.
- Claude only gets Jarvis's own tools, served in-process as the MCP server "jarvis"
  (tool names mcp__jarvis__<tool>). Every call runs through the gatekeeper via run_tool.
- Claude Code's built-in tools (Bash, Write, Read, ...) are kept out three ways:
  disallowed_tools, the can_use_tool callback and a PreToolUse hook that denies any
  tool not under mcp__jarvis__. User/project settings and other MCP servers are ignored.

The SDK is async; Jarvis is threaded. The client lives on its own event loop thread
and ask() is the blocking entry point used by the voice / HUD threads.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import threading
from pathlib import Path
from typing import Any, Callable

SERVER_NAME = "jarvis"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"

# Claude Code built-ins. Removed from Claude's context; the hook also denies anything
# not under TOOL_PREFIX, so a built-in missing from this list still can't run.
BUILTIN_TOOLS = [
    "Agent", "Task", "Bash", "BashOutput", "KillShell", "Read", "Write", "Edit", "MultiEdit",
    "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite", "Skill",
    "SlashCommand", "ExitPlanMode", "ListMcpResourcesTool", "ReadMcpResourceTool",
]

_WORKSPACE = Path(__file__).parent.parent.parent / "data" / "claude_workspace"


class BrainAborted(Exception):
    """The owner pressed Esc / said stop while Claude was working."""


def jarvis_tool_name(sdk_name: str) -> str | None:
    """'mcp__jarvis__open_app' -> 'open_app'; None for anything that isn't a Jarvis tool."""
    return sdk_name[len(TOOL_PREFIX):] if sdk_name.startswith(TOOL_PREFIX) else None


async def fence_permission(tool_name: str, input_data: dict, context: Any):
    """can_use_tool: only Jarvis tools may run (the gatekeeper inside each tool does the real checks)."""
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
    if jarvis_tool_name(tool_name):
        return PermissionResultAllow(updated_input=input_data)
    return PermissionResultDeny(message=f"{tool_name} is not available to Jarvis.")


async def fence_hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    """PreToolUse: runs for every call regardless of permission rules — deny non-Jarvis tools."""
    name = input_data.get("tool_name", "")
    if jarvis_tool_name(name):
        return {}
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"{name} is not available to Jarvis; use the jarvis tools.",
    }}


class ClaudeBrain:
    def __init__(self,
                 tool_schemas: list[dict],
                 run_tool: Callable[[Any, str, dict], str],
                 system_prompt: Callable[[], str],
                 model: str = "claude-opus-5-5",
                 max_turns: int = 15,
                 on_tool: Callable[[str, dict], None] | None = None,
                 on_tool_result: Callable[[str, str], None] | None = None,
                 on_usage: Callable[[Any], None] | None = None):
        self._tool_schemas = tool_schemas
        self._run_tool = run_tool
        self._system_prompt = system_prompt
        self.model = model
        self.max_turns = max_turns
        self._on_tool = on_tool
        self._on_tool_result = on_tool_result
        self._on_usage = on_usage
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._turn = None
        self._lock = threading.Lock()  # one request at a time on the shared session

    # ─── Blocking entry point (voice / HUD threads) ───

    def ask(self, prompt: str, turn: Any, abort_event: threading.Event | None = None) -> str:
        with self._lock:
            loop = self._ensure_loop()
            self._turn = turn
            fut = asyncio.run_coroutine_threadsafe(self._ask(prompt), loop)
            try:
                while True:
                    try:
                        return fut.result(timeout=0.25)
                    except concurrent.futures.TimeoutError:
                        if abort_event is not None and abort_event.is_set():
                            asyncio.run_coroutine_threadsafe(self._interrupt(), loop)
                            try:
                                fut.result(timeout=10)  # let the interrupted turn finish draining
                            except Exception:
                                pass
                            raise BrainAborted()
            finally:
                self._turn = None

    def close(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._reset(), self._loop).result(timeout=10)

    # ─── Event loop thread ───

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="claude-brain", daemon=True).start()
            self._loop = loop
        return self._loop

    async def _ask(self, prompt: str) -> str:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
        client = await self._connect()
        texts: list[str] = []
        result = None
        try:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    texts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    result = msg
        except Exception:
            await self._reset()  # broken session: reconnect on the next request
            raise
        if result is not None:
            if self._on_usage:
                self._on_usage(result)
            if result.is_error:
                # Claude Code puts the reason in `result` (e.g. "Failed to authenticate: OAuth
                # session expired") and often leaves `errors` empty.
                reason = "; ".join([*(result.errors or []), *([result.result] if result.result else [])])
                raise RuntimeError(f"Claude run failed ({result.subtype}): {reason or 'no reason given'}")
            if result.result:
                return result.result.strip()
        return "\n".join(t for t in texts if t).strip() or "Done."

    async def _connect(self):
        if self._client is None:
            from claude_agent_sdk import ClaudeSDKClient
            client = ClaudeSDKClient(options=self._options())
            await client.connect()
            self._client = client
        return self._client

    async def _interrupt(self) -> None:
        if self._client is not None:
            await self._client.interrupt()

    async def _reset(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, create_sdk_mcp_server
        server = create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=self._sdk_tools())
        _WORKSPACE.mkdir(parents=True, exist_ok=True)
        return ClaudeAgentOptions(
            system_prompt=self._system_prompt(),
            model=self.model,
            max_turns=self.max_turns,
            disallowed_tools=list(BUILTIN_TOOLS),
            mcp_servers={SERVER_NAME: server},
            strict_mcp_config=True,      # ignore .mcp.json, user MCP servers, connectors
            setting_sources=[],          # ignore user/project settings (and their allow rules)
            permission_mode="default",
            can_use_tool=fence_permission,
            hooks={"PreToolUse": [HookMatcher(hooks=[fence_hook])]},
            cwd=str(_WORKSPACE),         # empty folder, not the repo
        )

    def _sdk_tools(self) -> list:
        from claude_agent_sdk import tool
        tools = []
        for schema in self._tool_schemas:
            fn = schema["function"]
            tools.append(tool(fn["name"], fn["description"], fn["parameters"])(self._make_handler(fn["name"])))
        return tools

    def _make_handler(self, name: str):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            args = dict(args)
            if self._on_tool:
                self._on_tool(name, args)
            # Off the event loop: tools and approvals block (the confirm dialog waits up to 30 s).
            result = await asyncio.to_thread(self._run_tool, self._turn, name, args)
            if self._on_tool_result:
                self._on_tool_result(name, result)
            return {"content": [{"type": "text", "text": result}]}
        return handler
