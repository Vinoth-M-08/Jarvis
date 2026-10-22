from __future__ import annotations
import json
import re
import time
import threading
import msvcrt
from datetime import datetime
from pathlib import Path
import yaml
import numpy as np
import sounddevice as sd
import ollama
from openai import OpenAI

from jarvis.wake import listen_for_wake_word
from jarvis.stt import record_until_silence, transcribe_audio, set_abort_event
from jarvis.tts import speak, speak_streamed, is_speaking, stop_speaking
from jarvis.context import ContextManager
from jarvis.memory import Memory
from jarvis.tools.router import TOOL_SCHEMAS, dispatch
from jarvis.security.gatekeeper import USER_SOURCES, ConfirmRequest, Gatekeeper, TurnContext
from jarvis.security.confirm import ConfirmBroker
from jarvis.security.elevation import parse_command as parse_elevation_command
from jarvis.security.policy import Tier
from jarvis.brain.claude_agent import BrainAborted, ClaudeBrain
from jarvis.brain.claude_env import scrub_process_env
from jarvis.brain.usage import UsageTracker, today as usage_today
from jarvis.input_gate import InputGate
from jarvis.security.account import account_warning
from jarvis.router_cmds import CommandRouter, normalize as normalize_command
from jarvis.cache.answers import AnswerCache
from jarvis.tools.app_control import APP_MAP

_MAX_TOOL_LOOPS = 15
_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

# Global abort — Esc sets this, stops everything (speech + tool loop + follow-up)
_abort = threading.Event()
set_abort_event(_abort)  # Let STT check abort during recording
# Global mute — INSERT toggles this, skips TTS when set
_muted = threading.Event()

# --- Message bus: push events to all connected web clients ---
_event_listeners: list = []  # list of callables: fn(event_dict)


def register_event_listener(fn) -> None:
    _event_listeners.append(fn)


def _broadcast(event: dict) -> None:
    for fn in _event_listeners:
        try:
            fn(event)
        except Exception:
            pass


class _Aborted(Exception):
    """Raised when user hits Esc to abort the current action."""
    pass


def abort_all() -> None:
    """Stop everything Jarvis is doing right now."""
    _abort.set()
    stop_speaking()
    _broadcast({"type": "status", "message": "Stopped."})


def _is_stop_command(text: str) -> bool:
    """Check if transcribed text is a voice stop command."""
    cleaned = text.strip().lower().rstrip(".,!?")
    return cleaned in {
        "stop", "jarvis stop", "hey jarvis stop",
        "cancel", "abort", "shut up", "be quiet", "nevermind", "never mind",
    }


def _check_abort() -> None:
    """Raise _Aborted if user requested abort."""
    if _abort.is_set():
        raise _Aborted()


def is_muted() -> bool:
    return _muted.is_set()


def toggle_mute() -> None:
    if _muted.is_set():
        _muted.clear()
        _broadcast({"type": "mute", "muted": False})
        print("[Jarvis] Unmuted.")
    else:
        _muted.set()
        stop_speaking()
        _broadcast({"type": "mute", "muted": True})
        print("[Jarvis] Muted.")


def _speak_if_unmuted(text: str) -> None:
    """Speak text only if not muted."""
    if not _muted.is_set():
        speak(text)


def _speak_streamed_if_unmuted(text: str) -> None:
    """Speak streamed text only if not muted."""
    if not _muted.is_set():
        speak_streamed(text)


# --- OpenAI client cache (keyed by base_url) ---
_openai_clients: dict[str, OpenAI] = {}


def _get_openai_client(base_url: str, api_key: str = "lm-studio") -> OpenAI:
    if base_url not in _openai_clients:
        _openai_clients[base_url] = OpenAI(
            base_url=base_url, api_key=api_key, timeout=60.0,
        )
    return _openai_clients[base_url]


context = ContextManager()
memory = Memory()
gatekeeper = Gatekeeper()

with open(_CONFIG_PATH) as _f:
    _startup_cfg = yaml.safe_load(_f) or {}
_brain_cfg = _startup_cfg.get("brain") or {}
_cache_cfg = _startup_cfg.get("cache") or {}

command_router = CommandRouter(set(APP_MAP))
answer_cache = AnswerCache(_cache_cfg.get("db", "data/cache.db"),
                           ttl_days=float((_cache_cfg.get("answers") or {}).get("ttl_days", 7)))
_answer_cache_on = bool((_cache_cfg.get("answers") or {}).get("enabled", True))
_CLEAR_CACHE = {"clear cache", "clear the cache", "clear your cache", "forget cached answers"}
input_gate = InputGate(
    daily_claude_budget=int(_brain_cfg.get("daily_claude_budget", 100)),
    warn_at_percent=int(_brain_cfg.get("warn_at_percent", 80)),
)
usage_tracker = UsageTracker(_brain_cfg.get("usage_db", "data/usage.db"))


def _listen_short_answer() -> str:
    """Record one short spoken answer (for yes/no confirmations)."""
    from jarvis.wake import pause_wake_mic, resume_wake_mic
    pause_wake_mic()
    time.sleep(0.15)
    try:
        audio = record_until_silence(max_seconds=6)
    finally:
        resume_wake_mic()
    return transcribe_audio(audio)


confirm_broker = ConfirmBroker(
    broadcast=_broadcast,
    speak=_speak_if_unmuted,
    listen=_listen_short_answer,
    abort_event=_abort,
    timeout_s=gatekeeper.policy.confirm_timeout_s,
)
gatekeeper.confirmer = confirm_broker.ask
gatekeeper.elevation.on_change = lambda active, remaining: _broadcast(
    {"type": "elevation", "active": active, "remaining_s": remaining})
gatekeeper.elevation.on_expire = lambda: _speak_if_unmuted("Full access ended.")


def elevation_state() -> dict:
    return {"type": "elevation", "active": gatekeeper.elevation.active(),
            "remaining_s": gatekeeper.elevation.remaining_s()}


def _handle_elevation_command(command: tuple[str, int], turn: TurnContext) -> str:
    """Grant or end temporary full access. Granting always needs a click."""
    action, minutes = command
    if action == "revoke":
        gatekeeper.elevation.revoke()
        gatekeeper.audit.record(turn.id, turn.source, "full_access", {"action": "revoke"}, None, "allow", "revoked")
        return "Full access is off."
    minutes = max(1, min(minutes, gatekeeper.elevation.max_minutes))
    reason = f"actions that normally need your OK will run without asking for {minutes} minutes"
    req = ConfirmRequest(turn, "full_access", {"minutes": minutes}, Tier.STRONG, reason)
    if not confirm_broker.ask(req):
        gatekeeper.audit.record(turn.id, turn.source, "full_access", {"minutes": minutes}, 2, "deny", "not approved")
        return "Okay, full access stays off."
    granted = gatekeeper.elevation.grant(minutes)
    gatekeeper.audit.record(turn.id, turn.source, "full_access", {"minutes": granted}, 2, "allow", reason)
    return (f"Full access on for {granted} minutes. Deleting, overwriting and protected folders "
            "still need you or stay blocked.")


def _now() -> str:
    return datetime.now().strftime("%A, %B %d %Y, %I:%M %p")


def _system_prompt(with_time: bool = True) -> str:
    """with_time=False for the persistent Claude session, which gets the time with each request instead."""
    return (
        "You are Jarvis, a fully agentic personal AI assistant with screen vision and desktop control. "
        "Be concise, confident, and helpful. Do what your owner asks, within the safety rules below.\n\n"
        f"{gatekeeper.policy_prompt()}\n\n"
        "AGENTIC WORKFLOW for UI tasks:\n"
        "1. focus_window — bring app to front\n"
        "2. find_on_screen — locate text/buttons (returns x,y coordinates)\n"
        "3. click_at — click the coordinates\n"
        "4. Wait 1-2s for UI to update, then read_screen or find_on_screen to verify\n"
        "5. Repeat until task is done. You can chain up to 15 tool calls.\n\n"
        "TIPS:\n"
        "- After clicking, always verify the result before proceeding\n"
        "- If text not found, try scroll_screen then find_on_screen again\n"
        "- For typing in fields: click_at the field first, then type_text\n"
        "- Use press_key for keyboard shortcuts (ctrl+t, alt+f4, etc)\n"
        "- If a tool fails, try once more before giving up\n\n"
        "Answer in 1-3 sentences unless more is clearly needed."
        + (f" Current date and time: {_now()}." if with_time else "")
    )


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _save_config(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


def get_providers() -> dict:
    """Return the providers dict from config."""
    cfg = _load_config()
    return cfg.get("llm", {}).get("providers", {})


def get_active_provider() -> str:
    """Return the active provider key."""
    cfg = _load_config()
    return cfg.get("llm", {}).get("active_provider", "ollama")


def set_active_provider(provider_key: str) -> bool:
    """Switch the active provider. Returns True on success."""
    cfg = _load_config()
    providers = cfg.get("llm", {}).get("providers", {})
    if provider_key not in providers:
        return False
    cfg["llm"]["active_provider"] = provider_key
    _save_config(cfg)
    _broadcast({"type": "provider_changed", "provider": provider_key})
    return True


def _play_beep() -> None:
    """Short beep to signal processing has started."""
    try:
        t = np.linspace(0, 0.12, int(24000 * 0.12), endpoint=False)
        tone = 0.3 * np.sin(2 * np.pi * 600 * t).astype(np.float32)
        sd.play(tone, samplerate=24000)
        sd.wait()
    except Exception:
        pass


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks from LLM output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_tool_args(raw) -> dict:
    """OpenAI returns JSON string, Ollama returns dict. Handle both."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _call_openai_provider(provider_cfg: dict, temperature: float, full_messages: list[dict],
                          turn: TurnContext) -> str:
    """Call any OpenAI-compatible provider (LM Studio, NVIDIA NIM, etc.)."""
    model = provider_cfg["model"]
    base_url = provider_cfg["base_url"]
    api_key = provider_cfg.get("api_key", "lm-studio")
    client = _get_openai_client(base_url, api_key)
    tool_count = 0
    for _ in range(_MAX_TOOL_LOOPS):
        _check_abort()
        resp = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=TOOL_SCHEMAS,
            temperature=temperature,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            full_messages.append(msg.model_dump())
            for tc in msg.tool_calls:
                _check_abort()
                args = _parse_tool_args(tc.function.arguments)
                tool_count += 1
                _broadcast({"type": "tool", "name": tc.function.name, "args": args})
                if tool_count == 4:
                    _speak_if_unmuted("Working on it.")
                result = _exec_tool_with_retry(turn, tc.function.name, args)
                print(f"[Tool: {tc.function.name}] {result[:120]}")
                _broadcast({"type": "tool_result", "name": tc.function.name, "result": result[:200]})
                full_messages.append({
                    "role": "tool",
                    "content": result,
                    "tool_call_id": tc.id,
                })
                if turn.stopped:
                    return turn.stop_message()
        else:
            return _strip_think(msg.content or "Done.")
    return "Done."


def _call_ollama_provider(provider_cfg: dict, temperature: float, full_messages: list[dict],
                          turn: TurnContext) -> str:
    """Call Ollama provider."""
    model = provider_cfg["model"]
    think = provider_cfg.get("think", False)
    tool_count = 0
    for _ in range(_MAX_TOOL_LOOPS):
        _check_abort()
        response = ollama.chat(model=model, messages=full_messages, tools=TOOL_SCHEMAS, think=think)
        if response.message.tool_calls:
            full_messages.append(response.message.model_dump())
            for tc in response.message.tool_calls:
                _check_abort()
                args = _parse_tool_args(tc.function.arguments)
                tool_count += 1
                _broadcast({"type": "tool", "name": tc.function.name, "args": args})
                if tool_count == 4:
                    _speak_if_unmuted("Working on it.")
                result = _exec_tool_with_retry(turn, tc.function.name, args)
                print(f"[Tool: {tc.function.name}] {result[:120]}")
                _broadcast({"type": "tool_result", "name": tc.function.name, "result": result[:200]})
                full_messages.append({
                    "role": "tool",
                    "content": result,
                    "name": tc.function.name,
                })
                if turn.stopped:
                    return turn.stop_message()
        else:
            return _strip_think(response.message.content or "Done.")
    return "Done."


def _call_llm(full_messages: list[dict], turn: TurnContext) -> str:
    """Route to the active provider."""
    cfg = _load_config()
    llm_cfg = cfg["llm"]
    temperature = llm_cfg.get("temperature", 0.7)
    active = llm_cfg.get("active_provider", "ollama")
    providers = llm_cfg.get("providers", {})
    provider = providers.get(active, {})
    ptype = provider.get("type", "ollama")

    if ptype == "claude_agent":
        # Claude is handled in _process_request; here it means Claude failed — use the local brain.
        provider = providers.get("ollama", {"model": "qwen3:4b"})
        ptype = "ollama"

    if ptype == "openai":
        return _call_openai_provider(provider, temperature, full_messages, turn)
    else:
        return _call_ollama_provider(provider, temperature, full_messages, turn)


# ─── Claude brain (Claude Agent SDK on your subscription) ───

_claude_brain: ClaudeBrain | None = None


def _active_provider_cfg() -> dict:
    llm_cfg = _load_config().get("llm", {})
    return llm_cfg.get("providers", {}).get(llm_cfg.get("active_provider", "ollama"), {})


def _run_tool_for_claude(turn: TurnContext | None, name: str, args: dict) -> str:
    return _exec_tool_with_retry(turn or gatekeeper.new_turn("system"), name, args)


def _on_claude_tool(name: str, args: dict) -> None:
    _broadcast({"type": "tool", "name": name, "args": args})


def _on_claude_tool_result(name: str, result: str) -> None:
    print(f"[Tool: {name}] {result[:120]}")
    _broadcast({"type": "tool_result", "name": name, "result": result[:200]})


def _on_claude_usage(result) -> None:
    usage = result.usage or {}
    event = {
        "type": "usage", "brain": "claude",
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cost_usd_estimate": result.total_cost_usd,
        "num_turns": result.num_turns,
    }
    print(f"[Claude] in={event['input_tokens']} out={event['output_tokens']} "
          f"cache_read={event['cache_read_input_tokens']} turns={event['num_turns']}")
    usage_tracker.record(event["input_tokens"], event["output_tokens"],
                         event["cache_read_input_tokens"], event["num_turns"])
    event["requests_today"] = usage_tracker.requests_on()
    event["daily_budget"] = input_gate.daily_claude_budget
    _broadcast(event)


def _get_claude_brain(provider_cfg: dict) -> ClaudeBrain:
    global _claude_brain
    model = provider_cfg.get("model", "claude-opus-5-5")
    max_turns = int(provider_cfg.get("max_turns", _MAX_TOOL_LOOPS))
    if _claude_brain is not None and (_claude_brain.model, _claude_brain.max_turns) == (model, max_turns):
        return _claude_brain
    if _claude_brain is not None:
        try:
            _claude_brain.close()
        except Exception:
            pass
    removed = scrub_process_env()
    if removed:
        print(f"[Claude] Removed {', '.join(removed)} from the environment so Claude uses your subscription login.")
    _claude_brain = ClaudeBrain(
        tool_schemas=TOOL_SCHEMAS,
        run_tool=_run_tool_for_claude,
        system_prompt=lambda: _system_prompt(with_time=False),
        model=model,
        max_turns=max_turns,
        on_tool=_on_claude_tool,
        on_tool_result=_on_claude_tool_result,
        on_usage=_on_claude_usage,
    )
    return _claude_brain


def _call_claude_provider(provider_cfg: dict, user_text: str, facts: list[str], turn: TurnContext) -> str:
    """One request on the persistent Claude session. Claude keeps the history, so only the new text is sent."""
    parts = [f"[{_now()}]"]
    if facts:
        parts.append("Relevant context from memory: " + "; ".join(facts))
    parts.append(user_text)
    try:
        text = _get_claude_brain(provider_cfg).ask("\n".join(parts), turn, _abort)
    except BrainAborted:
        raise _Aborted()
    if turn.stopped:
        return turn.stop_message()
    return text


def _exec_tool_with_retry(turn: TurnContext, name: str, args: dict) -> str:
    """Execute a tool through the gatekeeper, retry once on failure (never on a block)."""
    result = gatekeeper.run(turn, name, args, dispatch)
    if result.startswith("Tool '") and "failed:" in result:
        time.sleep(0.5)
        result = gatekeeper.run(turn, name, args, dispatch)
    return result


def handle_wake() -> None:
    """Called when wake word is detected. Full pipeline: listen → think → speak."""
    _abort.clear()
    try:
        _handle_wake_inner()
    except _Aborted:
        print("[Jarvis] Aborted.")
    except Exception as e:
        import traceback
        print(f"[ERROR] {e}")
        traceback.print_exc()
        if not _abort.is_set():
            _speak_if_unmuted("Sorry, something went wrong.")
    finally:
        _abort.clear()
        _broadcast({"type": "status", "message": "Ready."})
        print("[Jarvis] Listening for wake word...")


def _handle_wake_inner() -> None:
    if is_speaking():
        stop_speaking()
    print("[Jarvis] Wake word detected!")
    _broadcast({"type": "status", "message": "Wake."})
    _speak_if_unmuted("Yes?")

    print("[Jarvis] Recording...")
    _broadcast({"type": "status", "message": "Listening..."})

    # Pause wake word mic so STT can use the hardware exclusively
    from jarvis.wake import pause_wake_mic, resume_wake_mic
    pause_wake_mic()
    time.sleep(0.15)  # Give pyaudio time to release the mic
    try:
        audio = record_until_silence()
    finally:
        resume_wake_mic()  # Always resume wake word detection
    _check_abort()
    print(f"[Jarvis] Recorded {len(audio)/16000:.1f}s of audio, transcribing...")
    user_text = transcribe_audio(audio)
    if not user_text.strip():
        print("[Jarvis] Transcription empty — didn't catch anything.")
        _speak_if_unmuted("I didn't catch that.")
        _broadcast({"type": "status", "message": "Ready."})
        return
    print(f"[You] {user_text}")

    if _is_stop_command(user_text):
        abort_all()
        print("[Jarvis] Stopped. (voice)")
        raise _Aborted()

    response_text = _process_request(user_text, source="voice")
    _check_abort()

    print(f"[Jarvis] {response_text}")
    _broadcast({"type": "status", "message": "Speaking..."})
    _speak_streamed_if_unmuted(response_text)


def _handle_without_brain(user_text: str, turn: TurnContext) -> str | None:
    """Router commands and cached answers — 0 tokens. None means the brain is needed."""
    if normalize_command(user_text) in _CLEAR_CACHE:
        return f"Cleared {answer_cache.clear()} cached answers."

    routed = command_router.match(user_text)
    if routed is not None:
        if routed.reply is not None:
            return routed.reply
        _broadcast({"type": "tool", "name": routed.tool, "args": routed.args})
        result = _exec_tool_with_retry(turn, routed.tool, routed.args)
        _broadcast({"type": "tool_result", "name": routed.tool, "result": result[:200]})
        if result.startswith("BLOCKED"):
            return f"I didn't do that: {turn.blocked[-1]}." if turn.blocked else "I didn't do that."
        return result

    if _answer_cache_on:
        cached = answer_cache.get(user_text)
        if cached is not None:
            print("[Cache] Answered from cache.")
            return cached
    return None


def _process_request(user_text: str, source: str = "typed") -> str:
    """Build context, call LLM, update memory. Returns response text.

    source is who started the turn ("voice", "typed", or a background trigger);
    only voice and typed turns may run acting tools.
    """
    _check_abort()
    turn = gatekeeper.new_turn(source)
    _play_beep()
    _broadcast({"type": "user", "text": user_text})

    elevation_cmd = parse_elevation_command(user_text)
    if elevation_cmd and source in USER_SOURCES:
        response_text = _handle_elevation_command(elevation_cmd, turn)
        _broadcast({"type": "response", "text": response_text})
        return response_text

    provider = _active_provider_cfg()
    uses_claude = provider.get("type") == "claude_agent"
    gate = input_gate.evaluate(
        user_text, source, uses_claude,
        claude_requests_today=usage_tracker.requests_on() if uses_claude else 0,
        today=usage_today(),
    )
    if gate.refuse:
        gatekeeper.audit.record(turn.id, turn.source, "input_gate", {"text": user_text[:200]}, 3, "deny", gate.reason)
        response_text = gate.refusal_text()
        _broadcast({"type": "response", "text": response_text})
        return response_text

    quick = _handle_without_brain(user_text, turn)
    if quick is not None:
        _broadcast({"type": "response", "text": quick})
        return quick

    _broadcast({"type": "status", "message": "Thinking..."})

    facts = memory.search_facts(user_text)
    response_text = None

    if uses_claude and not gate.use_local:
        try:
            response_text = _call_claude_provider(provider, user_text, facts, turn)
        except _Aborted:
            raise
        except Exception as e:
            print(f"[WARN] Claude brain failed ({e}), using the local brain")

    if response_text is None:
        messages = context.get_messages()
        if facts:
            facts_block = "Relevant context from memory: " + "; ".join(facts)
            messages = [{"role": "system", "content": facts_block}] + messages
        messages.append({"role": "user", "content": user_text})

        full_messages = [{"role": "system", "content": _system_prompt()}] + messages

        try:
            response_text = _call_llm(full_messages, turn)
        except _Aborted:
            raise
        except Exception as e:
            print(f"[WARN] Primary provider failed ({e}), trying Ollama fallback")
            cfg = _load_config()
            ollama_provider = cfg["llm"]["providers"].get("ollama", {"model": "qwen3:4b"})
            response_text = _call_ollama_provider(ollama_provider, cfg["llm"].get("temperature", 0.7), full_messages, turn)

        # The local brain is stateless, so it needs this history; the Claude session keeps its own.
        context.add("user", user_text)
        context.add("assistant", response_text)

    if _answer_cache_on and turn.tool_calls == 0 and not turn.stopped:
        answer_cache.put(user_text, response_text)
    if gate.notice:
        response_text = f"{response_text} {gate.notice}"
    memory.extract_and_store_facts(response_text, user_text)

    _broadcast({"type": "response", "text": response_text})
    return response_text



def _keyboard_listener() -> None:
    """Background thread: Esc = abort, F2 = type command, INSERT = mute/unmute."""
    while True:
        try:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                # Escape key
                if key == b'\x1b':
                    abort_all()
                    print("\n[Jarvis] Stopped. (Esc)")
                # Extended keys: F2 = 0x00+0x3c, INSERT = 0xe0+0x52
                elif key in (b'\x00', b'\xe0'):
                    special = msvcrt.getch()
                    if special == b'<':  # F2
                        print("\n[Type your command] ", end="", flush=True)
                        cmd = input()
                        if cmd.strip():
                            _handle_typed_command(cmd.strip())
                    elif special == b'R':  # INSERT
                        toggle_mute()
            time.sleep(0.05)
        except Exception:
            time.sleep(0.1)


def _handle_typed_command(text: str) -> None:
    """Process a typed command (same as voice, but from keyboard)."""
    _abort.clear()
    print(f"[You] {text}")
    try:
        response = _process_request(text, source="typed")
        print(f"[Jarvis] {response}")
        _speak_streamed_if_unmuted(response)
    except _Aborted:
        print("[Jarvis] Stopped.")
    finally:
        _abort.clear()


def main() -> None:
    import webbrowser
    from jarvis.web import start_web_background

    print("[Jarvis] Starting up...")
    print("[Jarvis] Keys: Esc = stop | F2 = type | INSERT = mute/unmute")

    start_web_background(port=7860)
    print("[Jarvis] Web UI: http://localhost:7860")

    threading.Thread(target=_keyboard_listener, daemon=True).start()

    webbrowser.open("http://localhost:7860")

    warning = account_warning()
    if warning:
        print(f"[WARN] {warning}")
        _speak_if_unmuted("Warning: I'm running with administrator rights.")

    _speak_if_unmuted("Good morning. Jarvis online.")
    listen_for_wake_word(handle_wake)


if __name__ == "__main__":
    main()
