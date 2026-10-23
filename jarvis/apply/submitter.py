"""Approved applications -> filled and submitted, one at a time, by one worker thread that owns
the browser (Playwright's sync API is single-threaded). Also opens previews (filled, not sent).

Per application: skip if the same job was already sent (another source) -> stop at the daily
limit -> re-read the live form (it may have changed) -> fill -> screenshot -> submit -> record.
CAPTCHA / emailed-code / unclear results stay open in the window for you (status "handoff").
"""
from __future__ import annotations
import queue
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from jarvis.apply.store import ApplyStore, slugify


def _same_job(a: dict, b: dict) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()
    return norm(a.get("company")) == norm(b.get("company")) and norm(a.get("role")) == norm(b.get("role"))


class Submitter:
    def __init__(self, store: ApplyStore, form_factory: Callable[[], object], recheck: Callable[[dict], dict],
                 on_change: Callable[[], None], notify: Callable[[str], None],
                 audit: Callable[[str, dict, str], None] = lambda tool, args, why: None,
                 daily_limit: int = 20, delay_s: tuple[float, float] = (20, 60)):
        self.store = store
        self._form_factory = form_factory
        self._recheck = recheck
        self._on_change = on_change
        self._notify = notify
        self._audit = audit
        self.daily_limit = daily_limit
        self.delay_s = delay_s
        self._tasks: queue.Queue = queue.Queue()
        self._form = None
        self._open_pages: list = []
        self.busy = False
        self._thread = threading.Thread(target=self._loop, name="apply-submit", daemon=True)
        self._thread.start()

    # ─── requests (any thread) ───
    def submit(self, app_ids: list[str]) -> None:
        self._tasks.put(("submit", list(app_ids)))

    def preview(self, app_id: str) -> None:
        self._tasks.put(("preview", [app_id]))

    def shutdown(self, wait_s: float = 15) -> None:
        """Close the browser — on the worker thread, which owns it."""
        closed = threading.Event()
        self._tasks.put(("close", [closed]))
        closed.wait(wait_s)

    def submitted_today(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return sum(1 for a in self.store.applications()
                   if a.get("status") == "submitted" and str(a.get("submitted_at", "")).startswith(today))

    # ─── worker ───
    def _loop(self) -> None:
        while True:
            kind, ids = self._tasks.get()
            if kind == "close":
                if self._form is not None:
                    self._form.close()
                    self._form = None
                ids[0].set()
                continue
            self.busy = True
            self._on_change()
            try:
                if kind == "preview":
                    self._preview(ids[0])
                else:
                    self._submit_all(ids)
            except Exception as e:
                self._notify(f"Applying stopped: {e}")
            finally:
                self.busy = False
                self._on_change()

    def _form_obj(self):
        if self._form is None:
            self._form = self._form_factory()
        return self._form

    def _preview(self, app_id: str) -> None:
        meta = self._recheck(self.store.application(app_id))
        if meta.get("status") not in ("ready", "approved"):
            self._notify(f"{meta.get('company', 'That application')} isn't ready to preview: it still has questions.")
            return
        form = self._form_obj()
        page = form.open(meta["form_url"])
        result = form.fill(page, meta.get("fields") or [])
        self._open_pages.append(page)
        self.store.update_application(app_id, previewed=_now())
        extra = f" I couldn't fill: {'; '.join(result.problems)}." if result.problems else ""
        self._notify(f"Preview of {meta.get('company')} is open in the apply window — nothing was submitted.{extra}")

    def _submit_all(self, ids: list[str]) -> None:
        done: dict[str, list[str]] = {"submitted": [], "handoff": [], "error": [], "parked": [], "duplicate": []}
        for n, app_id in enumerate(ids):
            if self.submitted_today() >= self.daily_limit:
                left = len(ids) - n
                self._notify(f"Daily limit of {self.daily_limit} reached — {left} approved application"
                             f"{'s' if left != 1 else ''} will go tomorrow.")
                break
            outcome = self._submit_one(app_id)
            if outcome:
                done.setdefault(outcome[0], []).append(outcome[1])
            self._on_change()
            if outcome and outcome[0] == "submitted" and n < len(ids) - 1:
                time.sleep(random.uniform(*self.delay_s))     # space them out
        parts = []
        if done["submitted"]:
            parts.append(f"Submitted {len(done['submitted'])}: {', '.join(done['submitted'])}.")
        if done["handoff"]:
            parts.append(f"{len(done['handoff'])} need you in the apply window: {', '.join(done['handoff'])}.")
        if done["error"]:
            parts.append(f"{len(done['error'])} didn't go through: {', '.join(done['error'])} — see the dashboard.")
        if done["parked"]:
            parts.append(f"{len(done['parked'])} got new questions: {', '.join(done['parked'])}.")
        if done["duplicate"]:
            parts.append(f"Skipped {len(done['duplicate'])} already sent from another source.")
        if parts:
            self._notify(" ".join(parts))

    def _submit_one(self, app_id: str) -> tuple[str, str] | None:
        meta = self.store.application(app_id)
        name = meta.get("company", app_id)
        if meta.get("status") != "approved":
            return None
        if any(_same_job(meta, other) and other.get("status") == "submitted" and other.get("job_id") != app_id
               for other in self.store.applications()):
            self.store.update_application(app_id, status="duplicate", note="already sent from another source")
            return "duplicate", name
        meta = self._recheck(meta)                    # the live form may have new questions
        if meta.get("status") != "approved":
            return "parked", name
        self.store.update_application(app_id, status="submitting")
        self._on_change()
        form = self._form_obj()
        page = form.open(meta["form_url"])
        filled = form.fill(page, meta.get("fields") or [])
        shots = self.store.root / "screenshots"
        before = form.screenshot(page, shots / f"{slugify(app_id, 60)}-filled.png")
        if filled.problems:
            self._open_pages.append(page)
            self.store.update_application(app_id, status="error", screenshot=before,
                                          note="couldn't fill: " + "; ".join(filled.problems)[:300])
            return "error", name
        self._audit("apply_submit", {"company": name, "role": meta.get("role"), "url": meta["form_url"]},
                    "approved by you")
        result = form.submit(page)
        after = form.screenshot(page, shots / f"{slugify(app_id, 60)}-result.png")
        if result.status == "submitted":
            self.store.update_application(app_id, status="submitted", submitted_at=_now(),
                                          screenshot=before, result_screenshot=after, note=result.message)
            self.store.log(f"submitted {name} — {meta.get('role')}")
            form.close_page(page)
            return "submitted", name
        self._open_pages.append(page)                 # leave it open for you
        status = "handoff" if result.status in ("code_needed", "captcha", "unknown") else "error"
        self.store.update_application(app_id, status=status, screenshot=before, result_screenshot=after,
                                      note=result.message)
        self.store.log(f"{status} {name}: {result.message}")
        return status, name


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
