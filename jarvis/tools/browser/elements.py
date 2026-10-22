"""Page elements and their safety flags, used by browser_read output and the gatekeeper (PLAN.md Phase 4).

The page script (EXTRACT_JS) only collects raw facts (tag, type, label, attributes);
everything security-relevant is decided here in Python, where it is testable.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse

# Runs in the page. Numbers the visible interactive elements (data-jarvis-id) and returns
# page text + raw element facts. Re-running it renumbers the page.
EXTRACT_JS = r"""
(maxEls) => {
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=searchbox],' +
              ' [role=textbox], [contenteditable=""], [contenteditable=true], [onclick]';
  document.querySelectorAll('[data-jarvis-id]').forEach(e => e.removeAttribute('data-jarvis-id'));
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= maxEls) break;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if ((el.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
    i += 1;
    el.setAttribute('data-jarvis-id', String(i));
    const form = el.form || el.closest('form');
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') ||
                   el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') || '')
                  .trim().replace(/\s+/g, ' ').slice(0, 80);
    out.push({
      index: i,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: (el.getAttribute('role') || '').toLowerCase(),
      label: label,
      href: el.href || '',
      download: el.hasAttribute('download'),
      attrs: [el.getAttribute('name'), el.id, el.getAttribute('autocomplete'), el.getAttribute('placeholder'),
              el.getAttribute('aria-label'), el.getAttribute('inputmode')].filter(Boolean).join(' ').toLowerCase(),
      in_form: !!form,
      form_role: form ? (form.getAttribute('role') || '').toLowerCase() : '',
      form_attrs: form ? [form.getAttribute('action'), form.id, form.getAttribute('name'),
                          form.getAttribute('class')].filter(Boolean).join(' ').toLowerCase() : '',
    });
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
  return {url: location.href, title: document.title, text: text, elements: out};
}
"""

_PASSWORD = re.compile(r"pass(word|wd|code)?\b|\bpwd\b")
_PAYMENT = re.compile(r"\bcc-|card|cvv|cvc|csc|expir|iban|\bbic\b|routing|account.?number|\botp\b|one.?time|"
                      r"verification.?code|security.?code|2fa|two.?factor|\bpin\b|\bupi\b")
_SEARCH = re.compile(r"search|\bquery\b|^q$|\bq\b|find")
_RISKY_ACTION = re.compile(
    r"\b(buy|pay|purchase|order|checkout|check out|place order|add to cart|send|post|publish|tweet|reply|"
    r"submit|confirm|accept|agree|allow|subscribe|unsubscribe|sign up|register|create account|log ?in|sign ?in|"
    r"delete|remove|cancel (?:my )?(?:order|subscription|account)|transfer|donate|book now|reserve|upload|share|"
    r"save changes|apply)\b")
_DOWNLOAD_EXT = re.compile(r"\.(exe|msi|msix|appx|zip|7z|rar|tar|gz|iso|img|dmg|apk|bat|cmd|ps1|vbs|js|jar|scr|reg)(\?|#|$)")


def is_password(el: dict) -> bool:
    return el.get("type") == "password" or bool(_PASSWORD.search(el.get("attrs", "")))


def is_payment(el: dict) -> bool:
    return bool(_PAYMENT.search(el.get("attrs", "")))


def is_text_field(el: dict) -> bool:
    if el.get("tag") == "textarea" or el.get("role") in ("textbox", "searchbox"):
        return True
    if el.get("tag") == "input":
        return el.get("type") not in ("submit", "button", "checkbox", "radio", "file", "image", "reset", "range", "color")
    return "contenteditable" in el.get("attrs", "") or el.get("tag") == "div" and el.get("role") == "textbox"


def is_search(el: dict) -> bool:
    if el.get("type") == "search" or el.get("role") == "searchbox" or el.get("form_role") == "search":
        return True
    return bool(_SEARCH.search(el.get("attrs", ""))) or "search" in el.get("form_attrs", "")


def is_download(el: dict) -> bool:
    href = el.get("href", "").lower()
    return bool(el.get("download")) or bool(_DOWNLOAD_EXT.search(urlparse(href).path))


def is_risky_action(el: dict) -> bool:
    """Buttons/links that submit, buy, send, delete, accept... Search submit buttons don't count."""
    clickable = el.get("tag") in ("button", "a") or el.get("type") in ("submit", "button", "image") or el.get("role") in ("button", "link")
    if not clickable:
        return False
    label = el.get("label", "").lower()
    if el.get("type") == "submit" and el.get("in_form") and (el.get("form_role") == "search" or "search" in el.get("form_attrs", "")):
        return False
    if _RISKY_ACTION.search(label):
        return True
    return el.get("type") == "submit" and el.get("in_form", False) and not _SEARCH.search(label)


def kind(el: dict) -> str:
    if el.get("tag") == "a" or el.get("role") == "link":
        return "link"
    if is_text_field(el):
        return "search box" if is_search(el) else "text field"
    if el.get("tag") == "select":
        return "dropdown"
    if el.get("type") in ("checkbox", "radio"):
        return el.get("type")
    return "button"


def format_element(el: dict) -> str:
    notes = []
    if is_password(el):
        notes.append("password field — owner types it")
    elif is_payment(el):
        notes.append("payment/code field — owner types it")
    if is_risky_action(el):
        notes.append("needs owner's click to use")
    if is_download(el):
        notes.append("download")
    line = f"[{el.get('index')}] {kind(el)} \"{el.get('label', '')}\""
    href = el.get("href", "")
    if href and kind(el) == "link":
        p = urlparse(href)
        line += f" → {p.netloc}{p.path[:40]}"
    if notes:
        line += f" ({'; '.join(notes)})"
    return line


def format_page(data: dict, max_chars: int = 6000) -> str:
    text = data.get("text", "")
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… (page text cut at {max_chars} characters)"
    els = "\n".join(format_element(e) for e in data.get("elements", [])) or "(none visible)"
    return (f"PAGE: {data.get('title', '')}\nURL: {data.get('url', '')}\n\n{text}\n\n"
            f"INTERACTIVE ELEMENTS (use the number with browser_click / browser_type):\n{els}")


def find_by_text(elements: list[dict], text: str) -> dict | None:
    t = text.strip().lower()
    if not t:
        return None
    exact = [e for e in elements if e.get("label", "").lower() == t]
    if exact:
        return exact[0]
    partial = [e for e in elements if t in e.get("label", "").lower()]
    return partial[0] if partial else None
