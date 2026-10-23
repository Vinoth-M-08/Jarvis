"""Fill and submit a Greenhouse application in a visible Chrome window (Playwright).

Field ids on Greenhouse forms are the API field names (first_name, email, resume,
question_123...), so every answer goes to its exact field:
  text / textarea -> typed;  file -> uploaded;  choice / yes-no -> the dropdown option is
  picked and checked;  multi-choice -> the matching checkbox ticked.
After submitting, the page is watched for: confirmation, an emailed security code,
a CAPTCHA challenge, or form errors. CAPTCHAs and codes are handed to you, never bypassed.
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

SUCCESS = re.compile(r"thank you for applying|thanks for applying|application (?:has been |was )?(?:submitted|received)|"
                     r"we(?:'ve| have) received your application|your application is complete", re.I)
CODE = re.compile(r"(?:verification|security) code|we(?:'ve| have)? sent (?:a|you a) code|enter the code|"
                  r"check your (?:email|inbox) for (?:a|the) code", re.I)


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # required fields we couldn't fill


@dataclass
class SubmitResult:
    status: str        # submitted | code_needed | captcha | errors | unknown
    message: str = ""


class GreenhouseForm:
    """One browser for many applications. Use from a single thread (Playwright sync API)."""

    def __init__(self, profile_dir: str | Path, headless: bool = False, slow_mo_ms: int = 40):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self._pw = None
        self._ctx = None

    def _context(self):
        if self._ctx is None:
            from playwright.sync_api import sync_playwright
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), channel="chrome", headless=self.headless, slow_mo=self.slow_mo_ms,
                viewport=None if not self.headless else {"width": 1280, "height": 900},
                args=["--start-maximized"] if not self.headless else [])
        return self._ctx

    def open(self, url: str):
        page = self._context().new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#first_name, form", timeout=30000)
        return page

    def fill(self, page, fields: list[dict]) -> FillResult:
        out = FillResult()
        for f in fields:
            name, kind, value, required = f["name"], f.get("kind", "text"), f.get("value"), f.get("required", False)
            label = f.get("label", name)
            if value in (None, ""):
                continue
            try:
                ok = self._fill_one(page, name, kind, str(value))
            except Exception as e:
                ok, why = False, str(e).splitlines()[0][:120]
            else:
                why = "the option didn't take" if not ok else ""
            if ok:
                out.filled.append(label)
            elif required:
                out.problems.append(f"{label}: {why or 'could not fill'}")
        return out

    def _fill_one(self, page, name: str, kind: str, value: str) -> bool:
        base = name.removesuffix("[]")
        if kind == "file":
            page.locator(f"input[type=file]#{_css(base)}").set_input_files(value)
            page.wait_for_timeout(800)
            return True
        if kind == "multi_choice":
            for label in page.locator(f'label[for^="{base}[]_"]').all():
                if label.inner_text().strip().lower() == value.strip().lower():
                    page.locator(f'input[id="{label.get_attribute("for")}"]').check()
                    return True
            return False
        box = page.locator(f"#{_css(base)}")
        box.scroll_into_view_if_needed()
        if kind in ("choice", "yes_no") or box.get_attribute("role") == "combobox":
            box.click()
            option = page.get_by_role("option", name=value, exact=True)
            if option.count() == 0:
                box.fill(value)
                page.wait_for_timeout(300)
                option = page.get_by_role("option", name=value, exact=True)
            if option.count():
                option.first.click()
            else:
                box.press("Enter")
            page.wait_for_timeout(200)
            shown = box.evaluate("e => (e.closest('[class*=select__control]') || e.parentElement).innerText") or ""
            return value.lower() in shown.lower()
        box.fill(value)
        return box.input_value() == value

    def screenshot(self, page, path: str | Path) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    def submit(self, page, wait_s: float = 40) -> SubmitResult:
        button = page.get_by_role("button", name=re.compile(r"submit application|^submit$", re.I))
        if button.count() == 0:
            return SubmitResult("errors", "no Submit button on the page")
        start_url = page.url
        button.first.click()
        deadline = time.time() + wait_s
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            text = page.locator("body").inner_text(timeout=5000)
            if "/confirmation" in page.url or (page.url != start_url and SUCCESS.search(text)) or SUCCESS.search(text):
                return SubmitResult("submitted", _first_line(SUCCESS, text))
            if CODE.search(text):
                return SubmitResult("code_needed", "the company emailed you a security code — enter it in the window")
            if _captcha_challenge(page):
                return SubmitResult("captcha", "a CAPTCHA appeared — solve it in the window and submit")
            errors = page.locator("[aria-invalid=true], .error-message, [class*=helper-text--error]").all_inner_texts()
            errors = [e.strip() for e in errors if e.strip()]
            if errors and time.time() > deadline - wait_s + 4:
                return SubmitResult("errors", "; ".join(errors[:3])[:200])
        return SubmitResult("unknown", "no confirmation within the wait — check the window")

    def close_page(self, page) -> None:
        try:
            page.close()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
            if self._pw is not None:
                self._pw.stop()
        finally:
            self._ctx = self._pw = None


def _css(ident: str) -> str:
    return re.sub(r"([^\w-])", r"\\\1", ident)


def _first_line(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    if not m:
        return ""
    line = text[max(0, text.rfind("\n", 0, m.start()) + 1): text.find("\n", m.end()) if "\n" in text[m.end():] else None]
    return line.strip()[:160]


def _captcha_challenge(page) -> bool:
    """A visible challenge (image grid) — not the invisible score check that runs on every submit."""
    for frame in page.frames:
        if re.search(r"recaptcha/.*/bframe|hcaptcha\.com/.*challenge|challenges\.cloudflare", frame.url):
            try:
                el = frame.frame_element()
                if el.is_visible() and (el.bounding_box() or {}).get("height", 0) > 100:
                    return True
            except Exception:
                continue
    return False
