"""Windows-MCP desktop control, run by Jarvis as an MCP client (PLAN.md Phase 3).

Jarvis starts `uvx windows-mcp serve` (stdio) with a fixed tool whitelist, lists the
tools, and exposes each one as `win_<name>` (win_click, win_snapshot, ...). Every call
goes through Jarvis's gatekeeper like any other tool — Windows-MCP itself has full
system access, so it must never be reachable directly.

Not loaded at all: Registry, FileSystem (Jarvis's file tools are folder-scoped),
Process (kill is disabled), Clipboard / Notification (Jarvis has its own), Scrape,
MultiSelect / MultiEdit, Screenshot (images are heavy; Snapshot gives labelled elements).

If Windows-MCP can't start (no uv, no network on first run, not Windows), Jarvis keeps
the older OCR + mouse tools instead.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import threading
from typing import Any

PREFIX = "win_"

DEFAULT_TOOLS = ["Snapshot", "Click", "Type", "Scroll", "Move", "Shortcut", "Wait", "WaitFor", "App", "PowerShell"]
NEVER_LOAD = {"Registry", "FileSystem", "Process", "Clipboard", "Notification", "Scrape",
              "MultiSelect", "MultiEdit", "Screenshot", "DisplayInventory", "ControlStatus"}

_MAX_DESCRIPTION = 600


def jarvis_name(mcp_name: str) -> str:
    return PREFIX + mcp_name.lower()


def _field(obj: Any, snake: str, camel: str, default: Any = None) -> Any:
    """Read an MCP model field: mcp 2.x uses snake_case (input_schema, is_error), 1.x camelCase."""
    value = getattr(obj, snake, None)
    return value if value is not None else getattr(obj, camel, default)


def to_function_schema(name: str, description: str, input_schema: dict | None) -> dict:
    """MCP tool -> the OpenAI-style function schema both brains use."""
    schema = dict(input_schema or {"type": "object", "properties": {}})
    props = dict(schema.get("properties") or {})
    props.pop("ctx", None)
    schema["properties"] = props
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r != "ctx"]
    schema.setdefault("type", "object")
    desc = (description or "").strip().replace("\n", " ")
    return {"type": "function", "function": {
        "name": jarvis_name(name),
        "description": ("[Windows desktop] " + desc)[:_MAX_DESCRIPTION],
        "parameters": schema,
    }}


def result_text(result: Any, name: str = "tool") -> str:
    """Flatten an MCP CallToolResult into the plain string Jarvis tools return."""
    parts = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(getattr(item, "text", ""))
        elif kind == "image":
            parts.append("[image omitted]")
        else:
            parts.append(f"[{kind} omitted]")
    text = "\n".join(p for p in parts if p).strip() or "Done."
    if _field(result, "is_error", "isError", False):
        return f"Tool '{name}' failed: {text}"   # same shape as router.dispatch errors (one retry)
    return text


class WindowsMCP:
    def __init__(self, command: str = "uvx", tools: list[str] | None = None,
                 start_timeout_s: float = 120, call_timeout_s: float = 60):
        wanted = [t for t in (tools or DEFAULT_TOOLS) if t not in NEVER_LOAD]
        self.command = command
        self.args = ["windows-mcp", "serve", "--tools", ",".join(wanted)]
        self.start_timeout_s = start_timeout_s
        self.call_timeout_s = call_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session = None
        self._stop: asyncio.Event | None = None
        self._tools: list = []
        self._names: dict[str, str] = {}   # win_click -> Click

    @property
    def running(self) -> bool:
        return self._session is not None

    def start(self) -> bool:
        """Start the server and list its tools. Blocking; False (with a log line) on failure."""
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, name="windows-mcp", daemon=True).start()
        self._loop = loop
        ready: concurrent.futures.Future = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(self._main(ready), loop)
        try:
            ready.result(timeout=self.start_timeout_s)
        except Exception as e:
            print(f"[Windows-MCP] Not available ({e}); using the OCR desktop tools instead.")
            self._session = None
            return False
        print(f"[Windows-MCP] Connected: {', '.join(self._names)}")
        return True

    async def _main(self, ready: concurrent.futures.Future) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from jarvis.brain.claude_env import child_env

        self._stop = asyncio.Event()
        env = child_env()
        env["ANONYMIZED_TELEMETRY"] = "false"
        params = StdioServerParameters(command=self.command, args=self.args, env=env)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self._tools = [t for t in listed.tools if t.name not in NEVER_LOAD]
                    self._names = {jarvis_name(t.name): t.name for t in self._tools}
                    self._session = session
                    if not ready.done():
                        ready.set_result(True)
                    await self._stop.wait()
        except Exception as e:
            if not ready.done():
                ready.set_exception(e)
            else:
                print(f"[Windows-MCP] Connection lost: {e}")
        finally:
            self._session = None

    def schemas(self) -> list[dict]:
        return [to_function_schema(t.name, t.description or "", _field(t, "input_schema", "inputSchema"))
                for t in self._tools]

    def tool_names(self) -> set[str]:
        return set(self._names)

    def call(self, name: str, args: dict) -> str:
        """Blocking call by Jarvis name (win_click). Raises if the server isn't running."""
        mcp_name = self._names.get(name)
        if mcp_name is None:
            return f"Unknown tool: {name}"
        if self._session is None or self._loop is None:
            return f"Tool '{name}' failed: Windows-MCP is not running"
        fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(mcp_name, arguments=args), self._loop)
        try:
            return result_text(fut.result(timeout=self.call_timeout_s), name)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            return f"Tool '{name}' failed: timed out after {self.call_timeout_s:.0f}s"
        except Exception as e:
            return f"Tool '{name}' failed: {e}"

    def close(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
