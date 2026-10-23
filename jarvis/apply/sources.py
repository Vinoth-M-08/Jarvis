"""Where jobs and application questions come from.

- JobOps (`/api/jobs`): the jobs, read-only.
- Greenhouse public job-board API: every question on a job's application form
  (labels, required, field types, options) — no browser, no login.
"""
from __future__ import annotations
import re

import httpx

from jarvis.apply.resolver import Question

JOBOPS_URL = "https://lambent-scone-b9805e.netlify.app/api/jobs"


def jobops_jobs(url: str = JOBOPS_URL) -> list[dict]:
    return httpx.get(url, timeout=60).json()["jobs"]


def clean(value) -> str:
    return re.sub(r"[​\s]+", " ", str(value or "")).strip()


def to_apply(jobs: list[dict]) -> list[dict]:
    """Passed the gates and not applied yet, best match first."""
    keep = [j for j in jobs if clean(j.get("Gate Result")) == "Pass" and clean(j.get("Status")) == "Not Applied"]
    return sorted(keep, key=lambda j: -_score(j.get("Job Match")))


def _score(match) -> int:
    m = re.match(r"\s*(\d+)", str(match or ""))
    return int(m.group(1)) if m else 0


_GH_KINDS = {"input_text": "text", "textarea": "textarea", "input_file": "file",
             "multi_value_single_select": "choice", "multi_value_multi_select": "multi_choice",
             "input_hidden": "hidden"}


def greenhouse_questions(board: str, job_id: str) -> tuple[list[Question], dict]:
    """(questions, job info) for a Greenhouse job. Raises httpx errors (closed job = 404)."""
    r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
                  params={"questions": "true"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    out: list[Question] = []
    for q in list(d.get("questions") or []) + list(d.get("location_questions") or []):
        fields = q.get("fields") or []
        main = fields[0] if fields else {}
        kind = _GH_KINDS.get(main.get("type", ""), "text")
        if kind == "hidden":
            continue
        options = [str(v.get("label")) for f in fields for v in (f.get("values") or [])]
        if kind == "choice" and {o.lower() for o in options} <= {"yes", "no"}:
            kind = "yes_no"
        out.append(Question(label=clean(q.get("label")), required=bool(q.get("required")), kind=kind,
                            options=options, field_name=str(main.get("name", ""))))
    # EEO / demographic questions are voluntary on Greenhouse: kept, but optional.
    for c in d.get("compliance") or []:
        for q in c.get("questions") or []:
            fields = q.get("fields") or []
            options = [str(v.get("label")) for f in fields for v in (f.get("values") or [])]
            out.append(Question(label=clean(q.get("label")), required=bool(q.get("required")),
                                kind="choice", options=options))
    return out, {"title": d.get("title"), "company": d.get("company_name"), "url": d.get("absolute_url")}
