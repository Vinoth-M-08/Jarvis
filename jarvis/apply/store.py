"""Everything auto-apply knows, as readable Markdown files in data/apply/ (git-ignored).

    profile.md                 your standard details (front matter you fill in)
    answers/<slug>.md          one saved answer per question (+ variants), reused forever
    inbox/<slug>.md            questions waiting for you + the applications parked on each
    applications/<id>.md       one record per job: status, every question and its answer
    log.md                     one line per run

Each file is YAML front matter between '---' lines, then free Markdown. You can edit any
of them by hand; Jarvis re-reads them every run.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

PROFILE_TEMPLATE = """---
# Your standard application details. Leave a field empty if you don't want Jarvis to use
# it — any required question that needs it is parked and asked once (see inbox/).
first_name:
last_name:
preferred_name:
email:
phone:
city:
country:
linkedin:
github:
portfolio:
current_company:
current_title:
years_experience:
notice_period:
current_ctc:
expected_ctc:
work_authorization:     # e.g. "Indian citizen, authorised to work in India"
needs_sponsorship:      # yes / no
willing_to_relocate:    # yes / no / which cities
highest_degree:
university:
graduation_year:
resumes:                # resume name used in JobOps -> full path of the PDF
  amazon:
  paypal:
  blueyonder:
  amex:
  angelone:
  base:
  p44:
---
# Profile

Fill in the fields above. Jarvis never guesses these; it asks (once) for anything missing.
"""


def slugify(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "question"


def read_doc(path: Path) -> tuple[dict, str]:
    """(front matter, body) of a Markdown file; ({}, text) if it has no front matter."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    return (yaml.safe_load(m.group(1)) or {}), m.group(2)


def write_doc(path: Path, meta: dict, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000).strip()
    path.write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")


@dataclass
class SavedAnswer:
    slug: str
    question: str
    answer: str
    variants: list[str] = field(default_factory=list)
    source: str = "you"
    used: int = 0


class ApplyStore:
    def __init__(self, root: str | Path = "data/apply"):
        self.root = Path(root)
        for sub in ("answers", "inbox", "applications", "screenshots"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        if not self.profile_path.exists():
            self.profile_path.write_text(PROFILE_TEMPLATE, encoding="utf-8")

    # ─── profile ───
    @property
    def profile_path(self) -> Path:
        return self.root / "profile.md"

    def profile(self) -> dict:
        meta, _ = read_doc(self.profile_path)
        return meta

    def set_profile(self, key: str, value) -> None:
        """Set one profile field, keeping the file's comments and layout (edits the one line)."""
        text = self.profile_path.read_text(encoding="utf-8")
        if "." in key:                                    # resumes.amazon
            parent, child = key.split(".", 1)
            pattern = re.compile(rf"^(\s+{re.escape(child)}:)[^\n#]*", re.MULTILINE)
            block = re.search(rf"^{re.escape(parent)}:.*?(?=^\S|^---)", text, re.MULTILINE | re.DOTALL)
            if block and pattern.search(block.group(0)):
                new_block = pattern.sub(lambda m: f"{m.group(1)} {_yaml_scalar(value)}", block.group(0), count=1)
                text = text.replace(block.group(0), new_block, 1)
            else:
                text = text.replace(f"{parent}:", f"{parent}:\n  {child}: {_yaml_scalar(value)}", 1)
        else:
            pattern = re.compile(rf"^({re.escape(key)}:)[^\n#]*", re.MULTILINE)
            if pattern.search(text):
                text = pattern.sub(lambda m: f"{m.group(1)} {_yaml_scalar(value)}", text, count=1)
            else:
                text = text.replace("\n---", f"\n{key}: {_yaml_scalar(value)}\n---", 1)
        self.profile_path.write_text(text, encoding="utf-8")

    # ─── answer bank ───
    def answers(self) -> list[SavedAnswer]:
        out = []
        for p in sorted((self.root / "answers").glob("*.md")):
            meta, _ = read_doc(p)
            if meta.get("answer") in (None, ""):
                continue
            out.append(SavedAnswer(slug=p.stem, question=str(meta.get("question", p.stem)),
                                   answer=str(meta["answer"]), variants=[str(v) for v in meta.get("variants") or []],
                                   source=str(meta.get("source", "you")), used=int(meta.get("used") or 0)))
        return out

    def save_answer(self, question: str, answer: str, source: str = "you", variants: list[str] | None = None) -> str:
        slug = slugify(question)
        path = self.root / "answers" / f"{slug}.md"
        meta = read_doc(path)[0] if path.exists() else {}
        known = set(meta.get("variants") or [])
        for v in variants or []:
            if v != question:
                known.add(v)
        write_doc(path, {"question": question, "variants": sorted(known), "answer": answer,
                         "source": f"{source}, {_today()}", "used": int(meta.get("used") or 0)},
                  "\nSaved answer. Edit `answer:` to change it everywhere.\n")
        return slug

    def mark_used(self, slug: str) -> None:
        path = self.root / "answers" / f"{slug}.md"
        if path.exists():
            meta, body = read_doc(path)
            meta["used"] = int(meta.get("used") or 0) + 1
            meta["last_used"] = _today()
            write_doc(path, meta, body)

    # ─── inbox (questions waiting for you) ───
    def park(self, slug: str, question: str, kind: str, application: str,
             options: list[str] | None = None, why: str = "") -> None:
        """Add a question to the inbox (or add this application to the ones waiting on it)."""
        path = self.root / "inbox" / f"{slug}.md"
        meta = read_doc(path)[0] if path.exists() else {
            "question": question, "kind": kind, "asked": _today(), "answer": None}
        if options:
            meta["options"] = options
        if why:
            meta["why"] = why
        waiting = list(meta.get("waiting") or [])
        if application not in waiting:
            waiting.append(application)
        meta["waiting"] = waiting
        write_doc(path, meta, "\nAnswer by voice, in the HUD, or write it after `answer:` above.\n")

    def inbox(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "inbox").glob("*.md")):
            meta, _ = read_doc(p)
            out.append({"slug": p.stem, **meta})
        return out

    def close_inbox(self, slug: str) -> list[str]:
        """Remove an answered question; returns the applications that were waiting on it."""
        path = self.root / "inbox" / f"{slug}.md"
        if not path.exists():
            return []
        meta, _ = read_doc(path)
        path.unlink()
        return list(meta.get("waiting") or [])

    # ─── application records ───
    def write_application(self, app_id: str, meta: dict, rows: list[tuple[str, str, str]]) -> None:
        """rows = (question, answer, source). Answers to sensitive questions are kept as given."""
        lines = ["", f"# {meta.get('company', '')} — {meta.get('role', '')}", "",
                 "| Question | Answer | From |", "|---|---|---|"]
        for q, a, s in rows:
            cell = lambda x: str(x).replace("|", "/").replace("\n", " ")[:160]
            lines.append(f"| {cell(q)} | {cell(a)} | {cell(s)} |")
        write_doc(self.root / "applications" / f"{slugify(app_id, 80)}.md", meta, "\n".join(lines) + "\n")

    def application(self, app_id: str) -> dict:
        path = self.root / "applications" / f"{slugify(app_id, 80)}.md"
        return read_doc(path)[0] if path.exists() else {}

    def applications(self) -> list[dict]:
        return [read_doc(p)[0] for p in sorted((self.root / "applications").glob("*.md"))]

    # ─── run log ───
    def log(self, line: str) -> None:
        path = self.root / "log.md"
        if not path.exists():
            path.write_text("# Auto-apply runs\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {datetime.now():%Y-%m-%d %H:%M} — {line}\n")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yaml_scalar(value) -> str:
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip().removesuffix("...").strip()
