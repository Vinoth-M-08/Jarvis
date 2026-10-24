"""Floating Jarvis: a small black pill at the top centre of the screen, just under the camera, that
shows what Jarvis is doing (listening, thinking, working, speaking) while you're away from the dashboard.

It appears when no dashboard tab is focused and hides when you go back to one (the page reports
focus over the WebSocket), and it's never shown while the PC is locked. It is click-through and
never takes focus, so it can sit over a browser's tab strip without getting in the way.

Drawn with PIL (2x supersampled, so the orb is smooth) into a Tk window whose corners are a
transparent colour key; Tk runs in its own thread and only that thread touches it.
"""
from __future__ import annotations
import math
import sys
import threading
import time
from typing import Callable

# state -> (label, colour)
STATES = {
    "idle": ("Jarvis", (120, 220, 255)),
    "listening": ("Listening", (63, 208, 255)),
    "thinking": ("Thinking", (155, 123, 255)),
    "working": ("Working", (75, 227, 138)),
    "speaking": ("Speaking", (150, 235, 255)),
    "approval": ("Needs your OK", (255, 181, 71)),
    "sleeping": ("Sleeping", (110, 120, 135)),
}
_KEY = "#010101"            # transparent colour key (the pill itself is pure black)


def state_for(event: dict, current: str) -> str:
    """Jarvis's broadcast events -> overlay state."""
    kind = event.get("type")
    if kind == "status":
        m = str(event.get("message", "")).lower()
        for prefix, state in (("listening", "listening"), ("wake", "listening"), ("thinking", "thinking"),
                              ("speaking", "speaking"), ("sleeping", "sleeping"), ("ready", "idle"),
                              ("stopped", "idle"), ("locked", "idle")):
            if m.startswith(prefix):
                return state
        return current
    if kind == "user":
        return "thinking"
    if kind == "tool":
        return "working"
    if kind == "tool_result":
        return "thinking"
    if kind == "confirm_request":
        return "approval"
    if kind == "confirm_resolved":
        return "thinking"
    return current


class Overlay:
    def __init__(self, speaking: Callable[[], bool] = lambda: False, level: Callable[[], float] = lambda: 0.0,
                 locked: Callable[[], bool] = lambda: False, width: int = 210, height: int = 40, top: int = 6):
        self._speaking, self._level, self._locked = speaking, level, locked
        self.state = "idle"
        self._clients: dict[int, bool] = {}       # dashboard connection -> focused
        self._lock = threading.Lock()
        self._w, self._h, self._top = width, height, top
        self._alpha = 0.0
        self._thread: threading.Thread | None = None
        self._levels = [0.0] * 7
        self._cache: dict = {}

    # ─── from other threads ───
    def on_event(self, event: dict) -> None:
        self.state = state_for(event, self.state)

    def dashboard_focus(self, client: int, focused: bool | None) -> None:
        """A dashboard tab gained / lost focus (None: it disconnected)."""
        with self._lock:
            if focused is None:
                self._clients.pop(client, None)
            else:
                self._clients[client] = focused

    @property
    def wanted(self) -> bool:
        with self._lock:
            dashboard_in_front = any(self._clients.values())
        return not dashboard_in_front and not self._locked()

    def shown_state(self) -> str:
        if self._speaking() and self.state not in ("approval",):
            return "speaking"
        return self.state

    def start(self) -> None:
        if self._thread is None and sys.platform == "win32":
            self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
            self._thread.start()

    # ─── drawing (pure: tests call it) ───
    def render(self, t: float, scale: float = 1.0):
        from PIL import Image, ImageDraw
        ss = 2                                          # supersample, then shrink: smooth edges
        w, h = round(self._w * scale), round(self._h * scale)
        W, H = w * ss, h * ss
        img = Image.new("RGB", (W, H), (1, 1, 1))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=(0, 0, 0))
        state = self.shown_state()
        label, colour = STATES.get(state, STATES["idle"])

        # orb: soft glow + core, breathing; a ring that spins while thinking / working
        cx, cy, r = H // 2 + 6 * ss, H // 2, H * 0.27
        pulse = 0.5 + 0.5 * math.sin(t * (6 if state in ("thinking", "working") else 2.4))
        if state == "speaking":
            pulse = min(1.0, self._level() * 8)
        if state == "sleeping":
            pulse = 0.15
        glow = self._glow(colour, r)
        size = max(2, round(glow.width * (0.8 + 0.25 * pulse)))
        glow = glow.resize((size, size), Image.BILINEAR).point(lambda v, f=0.45 + 0.45 * pulse: int(v * f))
        box = (round(cx - size / 2), round(cy - size / 2))
        region = img.crop((box[0], box[1], box[0] + size, box[1] + size))
        img.paste(_add(region, glow), box)
        img = Image.composite(img, Image.new("RGB", (W, H), (1, 1, 1)), self._mask(W, H))   # keep the glow inside the pill
        d = ImageDraw.Draw(img)
        core = r * (0.62 + 0.12 * pulse)
        d.ellipse((cx - core, cy - core, cx + core, cy + core), fill=colour)
        inner = core * 0.45
        d.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=(235, 250, 255))
        if state in ("thinking", "working", "approval"):
            ring = r * 1.12
            start = (t * 360 * (0.9 if state == "thinking" else 1.6)) % 360
            d.arc((cx - ring, cy - ring, cx + ring, cy + ring), start, start + 110, fill=colour, width=max(2, ss * 2))
            d.arc((cx - ring, cy - ring, cx + ring, cy + ring), start + 180, start + 250, fill=colour, width=max(2, ss * 2))

        # label
        font = self._font(int(13 * scale * ss))
        tx = cx + r * 1.9
        d.text((tx, cy), label, fill=(225, 232, 240) if state != "sleeping" else (130, 138, 150), font=font, anchor="lm")

        # voice bars on the right
        n, bw, gap = len(self._levels), 3 * scale * ss, 3 * scale * ss
        right = W - H * 0.45
        level = self._level() if state == "speaking" else 0.0
        for i in range(n):
            if state == "speaking":
                target = min(1.0, level * 9 * (0.55 + 0.45 * math.sin(t * 17 + i * 1.7) ** 2))
            elif state == "listening":
                target = 0.18 + 0.12 * math.sin(t * 3 + i * 0.9)
            elif state in ("thinking", "working"):
                target = 0.15 + 0.35 * max(0.0, math.sin(t * 5 - i * 0.8))
            else:
                target = 0.1
            self._levels[i] += (target - self._levels[i]) * 0.35
            bh = max(bw, self._levels[i] * H * 0.55)
            x0 = right - (n - i) * (bw + gap)
            d.rounded_rectangle((x0, cy - bh / 2, x0 + bw, cy + bh / 2), radius=bw / 2, fill=colour)
        small = img.resize((w, h), Image.BOX).point(lambda v: 2 if v == 1 else v)   # nothing inside is the key colour
        return Image.composite(small, Image.new("RGB", (w, h), (1, 1, 1)), self._edge(w, h, ss))

    # cached pieces (a blur per frame cost ~20 ms; these make a frame ~3 ms)
    def _glow(self, colour, r):
        key = ("glow", colour, round(r))
        if key not in self._cache:
            from PIL import Image, ImageDraw, ImageFilter
            size = round(r * 6)                         # room for the blur: no square edge
            g = Image.new("RGB", (size, size), (0, 0, 0))
            c = size / 2
            ImageDraw.Draw(g).ellipse((c - r * 1.4, c - r * 1.4, c + r * 1.4, c + r * 1.4), fill=colour)
            self._cache[key] = g.filter(ImageFilter.GaussianBlur(radius=r * 0.5))
        return self._cache[key]

    def _mask(self, W, H):
        key = ("mask", W, H)
        if key not in self._cache:
            from PIL import Image, ImageDraw
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=255)
            self._cache[key] = m
        return self._cache[key]

    def _edge(self, w, h, ss):
        """The pill's outline at the final size (inside = 255): everything outside is the key colour."""
        key = ("edge", w, h)
        if key not in self._cache:
            from PIL import Image
            self._cache[key] = self._mask(w * ss, h * ss).resize((w, h), Image.BOX).point(lambda v: 255 if v >= 128 else 0)
        return self._cache[key]

    def _font(self, size):
        key = ("font", size)
        if key not in self._cache:
            from PIL import ImageFont
            try:
                self._cache[key] = ImageFont.truetype("seguisb.ttf", size)
            except OSError:
                self._cache[key] = ImageFont.load_default()
        return self._cache[key]

    # ─── Tk thread ───
    def _run(self) -> None:
        try:
            self._tk_loop()
        except Exception as e:
            print(f"[Overlay] Off ({e}).")

    def _tk_loop(self) -> None:
        import ctypes
        import tkinter as tk
        from PIL import ImageTk
        user32 = ctypes.windll.user32
        try:   # sharp on a high-DPI screen: this thread's windows are DPI-aware (the rest of Jarvis is untouched)
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
        except Exception:
            pass
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", _KEY)
        root.attributes("-alpha", 0.0)
        root.configure(bg=_KEY)
        try:
            scale = user32.GetDpiForWindow(int(root.wm_frame(), 16)) / 96 or 1.0
        except Exception:
            scale = 1.0
        w, h = round(self._w * scale), round(self._h * scale)
        screen_w = user32.GetSystemMetrics(0)
        root.geometry(f"{w}x{h}+{(screen_w - w) // 2}+{round(self._top * scale)}")
        label = tk.Label(root, bg=_KEY, bd=0, highlightthickness=0)
        label.pack()
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # layered | click-through | no taskbar button | never takes focus
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        photo = {"img": None}

        def tick():
            target = 1.0 if self.wanted else 0.0
            self._alpha += (target - self._alpha) * 0.25
            if abs(self._alpha - target) < 0.02:
                self._alpha = target
            root.attributes("-alpha", round(self._alpha, 3))
            if self._alpha > 0:
                root.attributes("-topmost", True)
                photo["img"] = ImageTk.PhotoImage(self.render(time.time(), scale))
                label.configure(image=photo["img"])
                root.after(40, tick)                    # 25 fps while shown (~7 ms a frame)
            else:
                root.after(200, tick)                   # hidden: just check now and then

        tick()
        root.mainloop()


def _add(a, b):
    from PIL import ImageChops
    return ImageChops.add(a, b)
