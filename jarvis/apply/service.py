"""Auto-apply inside Jarvis: dashboard data, background runs, answers from voice or the HUD."""
from __future__ import annotations
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Callable

from datetime import datetime

from jarvis.apply import runner
from jarvis.apply.resolver import Resolver, match_option
from jarvis.apply.submitter import Submitter
from jarvis.apply.sources import clean, jobops_jobs
from jarvis.apply.store import ApplyStore, read_doc, slugify

ORDINALS = {"first": 0, "one": 0, "1": 0, "second": 1, "two": 1, "2": 1, "third": 2, "three": 2, "3": 2,
            "fourth": 3, "four": 3, "4": 3, "fifth": 4, "five": 4, "5": 4, "sixth": 5, "six": 5, "6": 5}
SKIP_WORDS = re.compile(r"^(?:skip|next|pass|later|ask (?:me )?later|i don'?t know|not sure|no idea)\b", re.I)
END_WORDS = re.compile(r"^(?:stop|that'?s (?:all|enough)|enough|done|finish|end|we'?re done)\b", re.I)
# Better typed than spoken: links and file paths go to the dashboard.
TYPE_IT = {"profile:linkedin", "profile:github", "profile:portfolio"}


def pick_option(spoken: str, options: list[str]) -> str | None:
    """'the second one' / 'option 3' / 'last' / 'within one month' -> one of the options."""
    t = re.sub(r"^(?:um|uh|so|ok(?:ay)?)[, ]+", "", spoken.lower().strip(" .!?"))
    direct = match_option(spoken, options)          # "within one month" is an option, not "option one"
    if direct is not None:
        return direct
    if re.fullmatch(r"(?:the )?last(?: one| option)?", t) and options:
        return options[-1]
    m = re.fullmatch(r"(?:the |option |number )?(first|second|third|fourth|fifth|sixth|one|two|three|four|five|six|[1-6])"
                     r"(?: one| option)?", t)
    if m:
        i = ORDINALS[m.group(1)]
        if i < len(options):
            return options[i]
    return None


def normalize_spoken(kind: str, text: str) -> str:
    """Speech -> what a form wants: 'gopi at gmail dot com' -> 'gopi@gmail.com'; phone digits."""
    t = text.strip().rstrip(".")
    if kind == "profile:email":
        t = re.sub(r"\s+at\s+", "@", t, flags=re.I)
        t = re.sub(r"\s+dot\s+", ".", t, flags=re.I)
        return re.sub(r"\s+", "", t).lower()
    if kind == "profile:phone":
        digits = re.sub(r"[^\d+]", "", t)
        return digits or t
    return t


def speakable(item: dict) -> str:
    """The question as Jarvis says it."""
    q = re.sub(r"\s*\*\s*$", "", str(item.get("question", "")))
    kind = str(item.get("kind", ""))
    if kind.startswith("profile:") and len(q.split()) <= 4:
        q = f"What's your {q.rstrip('?').lower()}?"
    opts = item.get("options") or []
    if opts:
        listed = ", ".join(opts[:-1]) + f", or {opts[-1]}" if len(opts) > 1 else opts[0]
        q += f" The options are: {listed}."
    return q


def find_resume_files(name: str, roots: list[Path] | None = None, limit: int = 5) -> list[str]:
    """PDFs that look like the '<name>' resume in your usual folders, best first."""
    home = Path.home()
    roots = roots if roots is not None else [home / "Documents", home / "Downloads", home / "Desktop",
                                             home / "OneDrive" / "Documents", home / "OneDrive" / "Desktop"]
    found: list[tuple[int, str]] = []
    want = name.lower()
    for root in roots:
        if not root.is_dir():
            continue
        for path in _pdfs(root, depth=3):
            low = path.name.lower()
            score = (3 if want and want in low else 0) + (2 if re.search(r"resume|cv\b|cv[_ -]", low) else 0)
            if score >= 2 or (want and want in low):
                found.append((score, str(path)))
    found.sort(key=lambda s: (-s[0], s[1]))
    return [p for _, p in found[:limit]]


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _pdfs(root: Path, depth: int):
    try:
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".pdf":
                yield entry
            elif entry.is_dir() and depth > 0 and not entry.name.startswith((".", "$")):
                yield from _pdfs(entry, depth - 1)
    except (PermissionError, OSError):
        return


class ApplyService:
    """One per Jarvis. Thread-safe: voice, HUD and background runs share it."""

    def __init__(self, root: str | Path, llm: Callable[[], Callable[[str], str] | None] = runner.local_llm,
                 on_change: Callable[[], None] | None = None, notify: Callable[[str], None] | None = None,
                 jobs: Callable[[], list[dict]] = jobops_jobs, form_factory: Callable[[], object] | None = None,
                 audit: Callable[[str, dict, str], None] | None = None, daily_limit: int = 20,
                 delay_s: tuple[float, float] = (20, 60), headless: bool = False):
        self.store = ApplyStore(root)
        self._llm = llm
        self._on_change = on_change or (lambda: None)
        self._notify = notify or (lambda text: None)
        self._jobs = jobs
        self._lock = threading.Lock()
        self.running = False
        for a in self.store.applications():           # Jarvis closed mid-submit: try again later
            if a.get("status") == "submitting":
                self.store.update_application(a["job_id"], status="approved")
        if form_factory is None:
            from jarvis.apply.greenhouse_form import GreenhouseForm
            form_factory = lambda: GreenhouseForm(self.store.root / "chrome-profile", headless=headless)
        self.submitter = Submitter(self.store, form_factory, self._recheck, self._on_change, self._notify,
                                   audit or (lambda tool, args, why: None), daily_limit, delay_s)

    # ─── approve / preview / submit ───
    def ready_ids(self) -> list[str]:
        return [a["job_id"] for a in self.store.applications() if a.get("status") == "ready"]

    def approve(self, app_ids: list[str] | None = None, by: str = "dashboard") -> list[str]:
        """Approve ready applications (all of them if app_ids is None) and queue them for submitting."""
        ready = set(self.ready_ids())
        ids = [i for i in (app_ids if app_ids is not None else sorted(ready)) if i in ready]
        for i in ids:
            self.store.update_application(i, status="approved", approved=f"{_stamp()} by {by}")
        if ids:
            self.store.log(f"approved {len(ids)} for submitting ({by})")
            self.submitter.submit(ids)
        self._on_change()
        return ids

    def preview(self, app_id: str) -> None:
        self.submitter.preview(app_id)

    def _recheck(self, meta: dict) -> dict:
        job = runner._job_from_record(meta)
        if not job:
            return meta
        with self._lock:
            return runner.check_application(self.store, Resolver(self.store, self._llm()), job)

    # ─── dashboard ───
    def snapshot(self) -> dict:
        apps = self.store.applications()
        counts = Counter(a.get("status") for a in apps)
        inbox = sorted(self.store.inbox(), key=lambda i: -len(i.get("waiting") or []))
        for item in inbox:
            kind = str(item.get("kind", ""))
            item["waiting_count"] = len(item.get("waiting") or [])
            item["input"] = "file" if kind.startswith("profile:resumes.") else (
                "choice" if item.get("options") else "text")
            if item["input"] == "file":
                item["suggestions"] = find_resume_files(kind.split(".", 1)[1])
        detected = self.store.root / "ats_detected.md"
        ats = read_doc(detected)[0].get("counts", {}) if detected.exists() else {}
        return {
            "summary": runner.summary(self.store), "running": self.running, "submitting": self.submitter.busy,
            "submitted_today": self.submitter.submitted_today(), "daily_limit": self.submitter.daily_limit,
            "counts": {k: counts.get(k, 0) for k in ("ready", "parked", "approved", "submitting", "submitted",
                                                     "handoff", "error", "failed", "later", "manual", "duplicate")},
            "inbox": inbox,
            "applications": [{k: a.get(k) for k in ("job_id", "company", "role", "status", "ats", "link", "match",
                                                    "checked", "note", "waiting_on", "resume", "submitted_at",
                                                    "screenshot", "result_screenshot", "form_url")} for a in apps],
            "ats": ats, "profile_path": str(self.store.profile_path),
        }

    def details(self, job_id: str) -> dict:
        path = self.store.root / "applications" / f"{slugify(job_id, 80)}.md"
        if not path.exists():
            return {}
        meta, body = read_doc(path)
        rows = []
        for line in body.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0] not in ("Question", "---"):
                rows.append({"question": cells[0], "answer": cells[1], "source": cells[2]})
        return {**meta, "rows": rows}

    # ─── answers (voice or HUD) ───
    def answer(self, slug: str, text: str) -> list[dict]:
        item = next((i for i in self.store.inbox() if i["slug"] == slug), None)
        if item is None:
            raise KeyError(slug)
        kind = str(item.get("kind", ""))
        text = normalize_spoken(kind, text)
        if item.get("options"):
            picked = pick_option(text, item["options"])
            if picked is None:
                raise ValueError(f"'{text}' isn't one of the options")
            text = picked
        if kind.startswith("profile:resumes.") and not Path(text.strip('"')).is_file():
            raise ValueError(f"No file at {text}")
        text = text.strip('"')
        with self._lock:
            out = runner.answer(self.store, slug, text, llm=self._llm())
        self._on_change()
        return {"answer": text, "rechecked": out}

    # ─── background run ───
    def run_in_background(self, limit: int = 10) -> bool:
        """Check the next `limit` jobs not looked at yet. False if a run is already going."""
        with self._lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._run, args=(limit,), name="apply-run", daemon=True).start()
        self._on_change()
        return True

    def _run(self, limit: int) -> None:
        try:
            jobs = self._jobs()
            done = {clean(a.get("job_id")) for a in self.store.applications()}
            fresh = [j for j in jobs if clean(j.get("Job ID")) not in done]
            with self._lock:
                results = runner.dry_run(self.store, fresh, limit=limit, llm=self._llm())
            c = Counter(r["status"] for r in results)
            n_q = len(self.store.inbox())
            text = (f"Job check done: {len(results)} checked — {c['ready']} ready, {c['parked']} parked"
                    + (f", {c['failed']} closed" if c["failed"] else "") + "."
                    + (f" I have {n_q} question{'s' if n_q != 1 else ''} for you — say 'ask me your questions' "
                       "when you're ready." if n_q else ""))
            if not results:
                text = "No new Greenhouse jobs to check right now."
        except Exception as e:
            text = f"The job check failed: {e}"
        finally:
            self.running = False
        self._notify(text)
        self._on_change()
