# tests/test_router_cmds.py
from datetime import datetime

import pytest

from jarvis.cache.answers import AnswerCache, cacheable
from jarvis.router_cmds import CommandRouter, normalize
from jarvis.security.gatekeeper import classify
from jarvis.security.policy import Policy, Tier

APPS = {"notepad", "chrome", "notepad++", "vs code", "calculator"}
NOW = datetime(2026, 10, 22, 15, 4)


@pytest.fixture
def router():
    return CommandRouter(APPS)


def test_normalize_strips_fillers():
    assert normalize("Hey Jarvis, please open Notepad!") == "open notepad"
    assert normalize("  Volume 40 please. ") == "volume 40"


@pytest.mark.parametrize("text,tool,args", [
    ("open notepad", "open_app", {"name": "notepad"}),
    ("Jarvis, launch Chrome", "open_app", {"name": "chrome"}),
    ("open notepad++", "open_app", {"name": "notepad++"}),
    ("start the calculator app", "open_app", {"name": "calculator"}),
    ("open youtube.com", "open_url", {"url": "https://youtube.com"}),
    ("go to github.com/Vinoth-M-08", "open_url", {"url": "https://github.com/vinoth-m-08"}),
    ("volume 40", "set_volume", {"level": 40}),
    ("set the volume to 150 percent", "set_volume", {"level": 100}),
    ("pause the music", "media_control", {"action": "pause"}),
    ("play", "media_control", {"action": "play"}),
    ("next song", "media_control", {"action": "next"}),
    ("previous track", "media_control", {"action": "previous"}),
    ("mute the sound", "media_control", {"action": "mute"}),
    ("scroll down", "scroll_screen", {"direction": "down", "amount": 3}),
    ("lock my computer", "lock_screen", {}),
    ("take a screenshot", "screenshot", {}),
    ("cpu usage", "get_system_info", {}),
    ("list open windows", "get_open_windows", {}),
    ("set a timer for 5 minutes", "set_timer", {"seconds": 300, "message": "5 minutes timer done"}),
])
def test_commands_map_to_tools(router, text, tool, args):
    m = router.match(text, NOW)
    assert m is not None and m.tool == tool and m.args == args, text


def test_time_and_date_answered_directly(router):
    assert router.match("what time is it?", NOW).reply == "It's 03:04 PM.".replace("03", "3")
    assert router.match("what's the date today", NOW).reply == "Today is Thursday, October 22, 2026."


@pytest.mark.parametrize("text,setting,value", [
    ("use the local brain", "brain", "local"),
    ("Jarvis, switch to Claude", "brain", "claude"),
    ("go back to claude", "brain", "claude"),
    ("switch to the backup model", "brain", "local"),
    ("use ollama", "brain", "local"),
    ("use sonnet", "claude_model", "sonnet"),
    ("Jarvis, switch to Claude Opus", "claude_model", "opus"),
    ("use the haiku model", "claude_model", "haiku"),
    ("change to sonnet", "claude_model", "sonnet"),
    ("to switch from Claude to Olamma model.", "brain", "local"),     # how Whisper heard it
    ("Can you switch from Claude to Ollama model?", "brain", "local"),
    ("change the model to ollama", "brain", "local"),
    ("I want you to switch to the local brain", "brain", "local"),
    ("switch over to the local brain", "brain", "local"),
    ("use the ollama model", "brain", "local"),
    ("switch back to cloud", "brain", "claude"),
    ("switch from ollama to claude", "brain", "claude"),
    ("What do you need from me?", "apply_status", None),
    ("Hey, can you mute yourself?", "mute", True),                       # failed on the 23rd
    ("Okay Jarvis, please mute yourself.", "mute", True),
    ("Maybe, can you mute yourself for now?", "mute", True),             # failed on the 24th
    ("So, actually, could you please go silent for a while.", "mute", True),
    ("Can you unmute yourself now, thanks", "mute", False),
    ("Jarvis, ask me your questions", "apply_questions", None),
    ("submit my applications", "apply_submit", None),
    ("send the ready applications now", "apply_submit", None),
    ("submit the ready ones", "apply_submit", None),
    ("let's go through the questions", "apply_questions", None),
    ("check new jobs", "apply_run", None),
    ("check the next 20 new jobs", "apply_run", 20),
    ("prepare my applications", "apply_run", None),
    ("apply for jobs", "apply_run", None),
    ("How are my job applications going?", "apply_status", None),
    ("Jarvis, fast mode on", "fast_chat", True),
    ("turn on fast replies", "fast_chat", True),
    ("fast mode", "fast_chat", True),
    ("turn off fast mode", "fast_chat", False),
    ("fast mode off", "fast_chat", False),
    ("which brain are you using?", "which_brain", None),
    ("what model is active", "which_brain", None),
    ("mute yourself", "mute", True),
    ("go silent", "mute", True),
    ("text only mode", "mute", True),
    ("unmute", "mute", False),
    ("talk to me again", "mute", False),
    ("you can speak now", "mute", False),
    ("speak faster", "speed", "faster"),
    ("talk slower please", "speed", "slower"),
    ("slow down a bit", "speed", "slower"),
    ("speed up", "speed", "faster"),
    ("set your voice speed to 1.3", "speed", 1.3),
    ("speak normally", "speed", "normal"),
    ("reset your speed", "speed", "normal"),
    ("forget that task", "forget_task", "last"),
    ("Jarvis, delete the last task", "forget_task", "last"),
    ("forget what I just did", "forget_task", "last"),
    ("forget today", "forget_task", "today"),
    ("forget everything I did today", "forget_task", "today"),
])
def test_settings_commands(router, text, setting, value):
    m = router.match(text, NOW)
    assert m is not None and m.setting == setting and m.value == value and m.tool is None, text


def test_settings_commands_are_not_brain_tools():
    # Only the router changes settings; no tool exists the brain could call instead.
    from jarvis.tools.router import TOOL_SCHEMAS
    names = {t["function"]["name"] if "function" in t else t.get("name") for t in TOOL_SCHEMAS}
    assert not names & {"set_active_provider", "set_provider", "toggle_mute", "set_tts_speed", "setting",
                         "set_claude_model", "claude_model"}


@pytest.mark.parametrize("text", [
    "mute the sound",           # PC audio, not Jarvis's voice
    "use claude to write an email",
    "use sonnet to summarise this page",
    "switch to chrome",
    "go to claude.ai",
])
def test_settings_lookalikes_are_not_settings(router, text):
    m = router.match(text, NOW)
    assert m is None or m.setting is None, text


@pytest.mark.parametrize("text", [
    "open chrome and search for laptops",
    "open the file report.pdf",
    "open my downloads folder",
    "what time does the shop close",
    "play despacito on youtube",
    "volume up a bit",
    "delete everything",
    "",
])
def test_longer_or_unknown_requests_go_to_brain(router, text):
    assert router.match(text, NOW) is None, text


# --- answer cache ---

@pytest.mark.parametrize("q", ["What is Docker?", "explain recursion", "who wrote Hamlet", "how does a VPN work"])
def test_general_questions_are_cacheable(q):
    assert cacheable(q)


@pytest.mark.parametrize("q", [
    "what's the weather", "what is the price of bitcoin", "what is on my desktop",
    "who am I", "what is this file", "what's the latest news", "open notepad", "what time is it",
])
def test_personal_or_time_sensitive_questions_are_not_cached(q):
    assert not cacheable(q)


def test_answer_cache_roundtrip_and_ttl(tmp_path):
    now = [1000.0]
    cache = AnswerCache(str(tmp_path / "c.db"), ttl_days=1, clock=lambda: now[0])
    assert cache.put("What is Docker?", "A container platform.")
    assert cache.get("what is docker") == "A container platform."
    assert not cache.put("what's the weather", "Sunny")
    now[0] += 2 * 86400
    assert cache.get("What is Docker?") is None
    assert cache.clear() == 1


# --- gatekeeper: open_app / open_url can't launch arbitrary programs ---

def _policy():
    return Policy(free_paths=[], blocked_paths=[], untrusted_tools=set())


def test_unknown_app_needs_confirm():
    assert classify("open_app", {"name": "notepad"}, _policy())[0] == Tier.FREE
    assert classify("open_app", {"name": r"C:\Users\me\Downloads\setup.exe"}, _policy())[0] == Tier.CONFIRM


def test_non_web_url_needs_confirm():
    assert classify("open_url", {"url": "https://example.com"}, _policy())[0] == Tier.FREE
    for url in [r"C:\Users\me\Downloads\x.exe", "file:///C:/x.exe", "ms-settings:", "javascript:alert(1)"]:
        assert classify("open_url", {"url": url}, _policy())[0] == Tier.CONFIRM, url
