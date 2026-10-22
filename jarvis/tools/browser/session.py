"""Visible Chrome driven by Playwright, with its own profile (PLAN.md Phase 4).

- Persistent profile in data/chrome-profile — none of your normal Chrome logins.
- Uses installed Google Chrome (channel "chrome"); falls back to Playwright's Chromium.
- Opens on the first browser tool call; the window stays so you can watch and take over.
- Downloads always land in ~/Downloads/Jarvis.
- Playwright's async API runs on its own event-loop thread; call() is the blocking entry
  point used through the gatekeeper.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus

from jarvis.tools.browser.elements import EXTRACT_JS, find_by_text, format_page, is_text_field

_REPO_ROOT = Path(__file__).parent.parent.parent.parent

SEARCH_ENGINES = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
}


def search_url(query: str, engine: str = "duckduckgo") -> str:
    return SEARCH_ENGINES.get(engine, SEARCH_ENGINES["duckduckgo"]).format(q=quote_plus(query))


def normalize_url(url: str) -> str | None:
    """Only web addresses. 'youtube.com' -> 'https://youtube.com'; file:, javascript:, etc. -> None."""
    u = url.strip()
    if re.match(r"^https?://", u, re.IGNORECASE):
        return u
    if re.match(r"^[a-z][a-z0-9+.-]*:", u, re.IGNORECASE):
        return None   # some other scheme
    if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", u):
        return "https://" + u
    return None


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "download"
    return name[:150]


class PageCache:
    """Text of pages already read, so the same page isn't re-sent to the brain (30 min)."""

    def __init__(self, ttl_s: float = 1800, clock=time.monotonic):
        self.ttl_s = ttl_s
        self._clock = clock
        self._items: dict[str, tuple[float, str, list]] = {}

    def get(self, url: str):
        item = self._items.get(url)
        if item and self._clock() - item[0] <= self.ttl_s:
            return item[1], item[2]
        return None

    def put(self, url: str, text: str, elements: list) -> None:
        self._items[url] = (self._clock(), text, elements)

    def invalidate(self, url: str | None = None) -> None:
        if url is None:
            self._items.clear()
        else:
            self._items.pop(url, None)


class BrowserSession:
    def __init__(self, profile_dir: str = "data/chrome-profile", channel: str | None = "chrome",
                 search_engine: str = "duckduckgo", downloads_dir: str | None = None,
                 max_chars: int = 6000, max_elements: int = 60, page_cache_minutes: float = 30,
                 call_timeout_s: float = 60):
        p = Path(profile_dir)
        self.profile_dir = p if p.is_absolute() else _REPO_ROOT / p
        self.channel = channel
        self.search_engine = search_engine
        self.downloads_dir = Path(downloads_dir) if downloads_dir else Path.home() / "Downloads" / "Jarvis"
        self.max_chars = max_chars
        self.max_elements = max_elements
        self.call_timeout_s = call_timeout_s
        self.cache = PageCache(ttl_s=page_cache_minutes * 60)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pw = None
        self._context = None
        self._page = None
        self._elements: dict[str, list[dict]] = {}   # url -> elements from the last read
        self._downloads: list[str] = []
        self._lock = threading.Lock()

    # ─── Gatekeeper hook ───

    def element_meta(self, tool: str, args: dict) -> dict | None:
        """Facts about the element a browser_click / browser_type call targets (from the last read)."""
        url = self._page.url if self._page is not None else ""
        elements = self._elements.get(url, [])
        if "index" in args and args["index"] is not None:
            try:
                idx = int(args["index"])
            except (TypeError, ValueError):
                return None
            return next((e for e in elements if e.get("index") == idx), None)
        if args.get("text"):
            return find_by_text(elements, str(args["text"]))
        return None

    # ─── Blocking entry point ───

    def call(self, tool: str, args: dict) -> str:
        with self._lock:
            loop = self._ensure_loop()
            fut = asyncio.run_coroutine_threadsafe(self._call(tool, args), loop)
            try:
                return fut.result(timeout=self.call_timeout_s)
            except concurrent.futures.TimeoutError:
                fut.cancel()
                return f"Tool '{tool}' failed: browser timed out after {self.call_timeout_s:.0f}s"
            except Exception as e:
                return f"Tool '{tool}' failed: {e}"

    def close(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout=15)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="browser", daemon=True).start()
            self._loop = loop
        return self._loop

    # ─── Event-loop side ───

    async def _start(self):
        if self._context is not None:
            return
        from playwright.async_api import async_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        kwargs = dict(user_data_dir=str(self.profile_dir), headless=False, accept_downloads=True, no_viewport=True)
        try:
            self._context = await self._pw.chromium.launch_persistent_context(channel=self.channel, **kwargs) \
                if self.channel else await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            if not self.channel:
                raise
            print(f"[Browser] Chrome not usable ({e}); using Playwright's Chromium.")
            self._context = await self._pw.chromium.launch_persistent_context(**kwargs)
        self._context.on("page", self._watch_page)
        for p in self._context.pages:
            self._watch_page(p)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    def _watch_page(self, page) -> None:
        page.on("download", lambda d: asyncio.ensure_future(self._save_download(d)))

    async def _save_download(self, download) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        target = self.downloads_dir / safe_filename(download.suggested_filename)
        await download.save_as(str(target))
        self._downloads.append(str(target))

    async def _shutdown(self):
        if self._context is not None:
            await self._context.close()
        if self._pw is not None:
            await self._pw.stop()
        self._context = self._pw = self._page = None

    async def _call(self, tool: str, args: dict) -> str:
        await self._start()
        if self._page is None or self._page.is_closed():
            self._page = self._context.pages[-1] if self._context.pages else await self._context.new_page()
        handler = getattr(self, "_t_" + tool.removeprefix("browser_"), None)
        if handler is None:
            return f"Unknown tool: {tool}"
        result = await handler(args)
        if self._downloads:
            saved, self._downloads = self._downloads, []
            result += "\nDownloaded to: " + ", ".join(saved)
        return result

    async def _settle(self):
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

    def _where(self) -> str:
        return f"Now on: {self._page.url}"

    async def _t_open(self, a: dict) -> str:
        url = normalize_url(str(a.get("url", "")))
        if url is None:
            return "Tool 'browser_open' failed: only http(s) web addresses can be opened"
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return f"Opened {url}. Use browser_read to see the page."

    async def _t_search(self, a: dict) -> str:
        url = search_url(str(a.get("query", "")), self.search_engine)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return await self._t_read({})

    async def _t_read(self, a: dict) -> str:
        url = self._page.url
        cached = self.cache.get(url) if not a.get("refresh") else None
        if cached is not None:
            text, elements = cached
            self._elements[url] = elements
            return "(from page cache — pass refresh=true to re-read)\n" + text
        data = await self._page.evaluate(EXTRACT_JS, self.max_elements)
        self._elements[data.get("url", url)] = data.get("elements", [])
        text = format_page(data, self.max_chars)
        self.cache.put(data.get("url", url), text, data.get("elements", []))
        return text

    async def _locate(self, a: dict):
        el = self.element_meta("browser_click", a)
        if el is None:
            return None, None
        return self._page.locator(f'[data-jarvis-id="{el["index"]}"]').first, el

    async def _t_click(self, a: dict) -> str:
        loc, el = await self._locate(a)
        if loc is None:
            return "Tool 'browser_click' failed: element not found — call browser_read first and use its numbers"
        self.cache.invalidate(self._page.url)
        await loc.click(timeout=10000)
        await self._settle()
        return f"Clicked [{el['index']}] \"{el.get('label', '')}\". {self._where()}"

    async def _t_type(self, a: dict) -> str:
        loc, el = await self._locate(a)
        if loc is None:
            return "Tool 'browser_type' failed: field not found — call browser_read first and use its numbers"
        text = str(a.get("value", ""))
        self.cache.invalidate(self._page.url)
        if is_text_field(el) and el.get("tag") in ("input", "textarea"):
            await loc.fill(text, timeout=10000)
        else:
            await loc.click(timeout=10000)
            await self._page.keyboard.type(text)
        if a.get("submit"):
            await loc.press("Enter")
            await self._settle()
            return f"Typed into [{el['index']}] and pressed Enter. {self._where()}"
        return f"Typed into [{el['index']}] \"{el.get('label', '')}\"."

    async def _t_scroll(self, a: dict) -> str:
        amount = int(a.get("amount", 3)) * 400
        dy = -amount if str(a.get("direction", "down")) == "up" else amount
        await self._page.mouse.wheel(0, dy)
        return "Scrolled. Use browser_read to see what's visible now."

    async def _t_back(self, a: dict) -> str:
        await self._page.go_back(wait_until="domcontentloaded", timeout=15000)
        return f"Went back. {self._where()}"

    async def _t_tabs(self, a: dict) -> str:
        action = str(a.get("action", "list"))
        pages = self._context.pages
        if action == "new":
            self._page = await self._context.new_page()
            return "Opened a new tab. Use browser_open to load a page in it."
        if action in ("switch", "close"):
            try:
                target = pages[int(a.get("index", -1))]
            except (ValueError, IndexError):
                return f"Tool 'browser_tabs' failed: no tab {a.get('index')}"
            if action == "close":
                await target.close()
                pages = self._context.pages
                self._page = pages[-1] if pages else await self._context.new_page()
                return f"Closed the tab. {self._where()}"
            self._page = target
            await target.bring_to_front()
            return f"Switched tab. {self._where()}"
        lines = []
        for i, p in enumerate(pages):
            title = await p.title()
            lines.append(f"[{i}] {title} — {p.url}" + ("  (current)" if p is self._page else ""))
        return "\n".join(lines) or "No tabs open."
