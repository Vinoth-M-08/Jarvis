"""Phase 1 runs: dry-run applications (read the form, answer or park — submit nothing),
answer parked questions (and re-check the applications waiting on them), ATS re-detection."""
from __future__ import annotations
import time
from collections import Counter
from typing import Callable

from jarvis.apply import ats as ats_mod
from jarvis.apply.resolver import Question, Resolver
from jarvis.apply.sources import clean, greenhouse_questions, to_apply
from jarvis.apply.store import ApplyStore, slugify, write_doc

SUPPORTED = {"greenhouse"}   # Greenhouse first; the other easy sources follow
DONE_STATUS = {"submitted", "handoff", "duplicate"}   # never re-checked
KEEP_STATUS = {"approved"}


def local_llm(model: str | None = None) -> Callable[[str], str] | None:
    """The local brain (Ollama) as prompt -> text, or None if it isn't running."""
    try:
        import ollama
        import yaml
        if model is None:
            with open("config.yaml", encoding="utf-8") as f:
                model = yaml.safe_load(f)["llm"]["providers"]["ollama"]["model"]
        ollama.ps()
    except Exception:
        return None

    def ask(prompt: str) -> str:
        r = ollama.chat(model=model, think=False, keep_alive="30m",
                        messages=[{"role": "user", "content": prompt}], options={"temperature": 0.2})
        return r.message.content or ""
    return ask


def job_meta(job: dict) -> dict:
    link = clean(job.get("Apply Link"))
    return {"job_id": clean(job.get("Job ID")), "company": clean(job.get("Company Name")),
            "role": clean(job.get("Role")), "ats": clean(job.get("ATS")) or ats_mod.from_text(link) or "unknown",
            "link": link, "resume": clean(job.get("Resume")), "match": clean(job.get("Job Match"))[:80],
            "location": clean(job.get("Location"))}


def check_application(store: ApplyStore, resolver: Resolver, job: dict) -> dict:
    """Dry run for one job: read its form, answer every question or park it. Submits nothing."""
    meta = job_meta(job)
    app_id = meta["job_id"] or slugify(f"{meta['company']}-{meta['role']}")
    prior = store.application(app_id)
    if prior.get("status") in DONE_STATUS:
        return prior                                  # submitted / handed to you: never re-checked
    ats = ats_mod.from_text(meta["link"]) or meta["ats"]
    meta.update(ats=ats, checked=time.strftime("%Y-%m-%d %H:%M"), mode="dry run (nothing submitted)")
    if ats not in SUPPORTED:
        meta.update(status="later", note=f"{ats}: not in phase 1")
        store.write_application(app_id, meta, [])
        return meta
    ids = _greenhouse_ids(meta["link"])
    if not ids:
        meta.update(status="manual", note="couldn't read the Greenhouse job id from the link")
        store.write_application(app_id, meta, [])
        return meta
    try:
        questions, info = greenhouse_questions(*ids)
    except Exception as e:
        closed = "404" in str(e)
        meta.update(status="failed" if closed else "error",
                    note="posting closed (Greenhouse returns 404)" if closed else f"couldn't read the form: {e}")
        store.write_application(app_id, meta, [])
        return meta

    rows, waiting, fields = [], [], []
    for q in questions:
        r = resolver.resolve(q, job)
        if r.park:
            slug, kind = r.park
            # Profile questions are asked plainly (one form's dropdown would make a bad profile value).
            store.park(slug, q.label, kind, app_id, options=(q.options or None) if kind == "answer" else None)
            waiting.append(slug)
            rows.append((q.label + (" *" if q.required else ""), "— waiting for you —", "parked"))
        else:
            rows.append((q.label + (" *" if q.required else ""), r.answer or "", r.source))
            if r.answer not in (None, "") and q.field_name:
                fields.append({"name": q.field_name, "kind": q.kind, "label": q.label,
                               "value": r.answer, "required": q.required})
    status = "parked" if waiting else "ready"
    if prior.get("status") in KEEP_STATUS and not waiting:   # re-checking doesn't undo your approval
        status = prior["status"]
    meta.update(status=status, waiting_on=sorted(set(waiting)), questions=len(questions),
                board=ids[0], gh_job=ids[1], form_url=f"https://job-boards.greenhouse.io/{ids[0]}/jobs/{ids[1]}",
                fields=fields)
    store.write_application(app_id, meta, rows)
    return meta


def _greenhouse_ids(link: str, fetch=None) -> tuple[str, str] | None:
    """(board, job id). Company career pages (bitwarden.com/careers/...?gh_jid=123) don't name the
    board in the link: look in the page, else try the company's name from the address."""
    ids = ats_mod.greenhouse_ids(link)
    if ids:
        return ids
    from urllib.parse import parse_qs, urlparse
    gh = parse_qs(urlparse(link).query).get("gh_jid")
    if not gh:
        return None
    try:
        _, html = (fetch or ats_mod._fetch)(link)
        ids = ats_mod.greenhouse_ids(link, html)
        if ids:
            return ids
    except Exception:
        pass
    host = urlparse(link).hostname or ""
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs")]
    return (parts[0], gh[0]) if parts else None


def dry_run(store: ApplyStore, jobs: list[dict], limit: int = 5, source: str = "greenhouse",
            llm: Callable[[str], str] | None = None, delay_s: float = 1.0) -> list[dict]:
    resolver = Resolver(store, llm)
    picked = [j for j in to_apply(jobs)
              if (ats_mod.from_text(clean(j.get("Apply Link"))) or clean(j.get("ATS")).lower()) == source]
    results = []
    for job in picked[:limit]:
        results.append(check_application(store, resolver, job))
        time.sleep(delay_s)   # be polite to the job board
    counts = Counter(r["status"] for r in results)
    store.log(f"dry run ({source}, {len(results)} jobs): " + ", ".join(f"{n} {s}" for s, n in counts.items()))
    return results


def answer(store: ApplyStore, slug: str, text: str, jobs_by_id: dict[str, dict] | None = None,
           llm: Callable[[str], str] | None = None) -> list[dict]:
    """Save your answer to an inbox question, then re-check every application parked on it."""
    item = next((i for i in store.inbox() if i["slug"] == slug), None)
    if item is None:
        raise KeyError(f"No open question '{slug}'")
    kind = str(item.get("kind", "answer"))
    if kind.startswith("profile:"):
        store.set_profile(kind.split(":", 1)[1], text)
    else:
        store.save_answer(str(item["question"]), text, source="you")
    waiting = store.close_inbox(slug)
    if not waiting:
        return []
    resolver = Resolver(store, llm)   # re-reads profile + answers
    out = []
    for app_id in waiting:
        job = (jobs_by_id or {}).get(app_id) or _job_from_record(store.application(app_id))
        if job:
            out.append(check_application(store, resolver, job))
    store.log(f"answered '{item['question']}' -> re-checked {len(out)} application(s)")
    return out


def _job_from_record(meta: dict) -> dict | None:
    if not meta:
        return None
    return {"Job ID": meta.get("job_id"), "Company Name": meta.get("company"), "Role": meta.get("role"),
            "Apply Link": meta.get("link"), "ATS": meta.get("ats"), "Resume": meta.get("resume"),
            "Job Match": meta.get("match"), "Location": meta.get("location"),
            "Gate Result": "Pass", "Status": "Not Applied"}


def detect_all(store: ApplyStore, jobs: list[dict], only_unclear: bool = True, delay_s: float = 0.5,
               fetch=None) -> Counter:
    """Re-detect the ATS of jobs marked other / blank (or all). Results -> data/apply/ats_detected.md."""
    rows, counts = [], Counter()
    for j in jobs:
        listed = clean(j.get("ATS")).lower()
        if only_unclear and listed not in ("", "other"):
            continue
        link = clean(j.get("Apply Link"))
        det = ats_mod.detect(link, fetch=fetch)
        if det.how == "page":
            time.sleep(delay_s)
        counts[det.ats] += 1
        rows.append({"job_id": clean(j.get("Job ID")), "company": clean(j.get("Company Name")),
                     "role": clean(j.get("Role")), "listed": listed or "(blank)", "detected": det.ats,
                     "how": det.how, "link": link})
    body = ["", "# ATS re-detection", "", f"{len(rows)} jobs listed as other / blank.", "",
            "| Detected | Jobs |", "|---|---|"]
    body += [f"| {a} | {n} |" for a, n in counts.most_common()]
    body += ["", "| Company | Role | Listed | Detected | How | Link |", "|---|---|---|---|---|---|"]
    body += [f"| {r['company']} | {r['role'][:50]} | {r['listed']} | **{r['detected']}** | {r['how']} | {r['link'][:80]} |"
             for r in sorted(rows, key=lambda r: r["detected"])]
    write_doc(store.root / "ats_detected.md",
              {"checked": time.strftime("%Y-%m-%d %H:%M"), "jobs": len(rows), "counts": dict(counts)},
              "\n".join(body) + "\n")
    store.log(f"ATS re-detection: {len(rows)} jobs -> " + ", ".join(f"{n} {a}" for a, n in counts.most_common()))
    return counts


def summary(store: ApplyStore) -> str:
    """One spoken-length sentence: what's ready and what needs you."""
    apps = store.applications()
    counts = Counter(a.get("status") for a in apps)
    inbox = store.inbox()
    if not apps and not inbox:
        return "No applications checked yet."
    parts = [f"{counts['ready']} ready", f"{counts['parked']} parked"]
    for key, word in (("approved", "approved"), ("submitted", "submitted"), ("handoff", "waiting in the apply window"),
                      ("error", "with errors"), ("failed", "closed")):
        if counts[key]:
            parts.append(f"{counts[key]} {word}")
    text = "Applications: " + ", ".join(parts) + "."
    if inbox:
        top = sorted(inbox, key=lambda i: -len(i.get("waiting") or []))[:3]
        asks = "; ".join(f"{i['question']} ({len(i.get('waiting') or [])} waiting)" for i in top)
        text += f" {len(inbox)} question{'s' if len(inbox) != 1 else ''} need you. Most needed: {asks}."
    else:
        text += " Nothing needs you."
    return text
