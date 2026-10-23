"""Auto-apply, phase 1 (nothing is submitted).

    python -m jarvis.apply detect-ats           re-detect the ATS of other/blank jobs
    python -m jarvis.apply dry-run [N]          read N Greenhouse forms, answer or park (default 5)
    python -m jarvis.apply inbox                questions waiting for you
    python -m jarvis.apply answer SLUG TEXT     answer one; parked applications are re-checked
    python -m jarvis.apply status               short summary
"""
from __future__ import annotations
import sys

from jarvis.apply import runner
from jarvis.apply.sources import clean, jobops_jobs
from jarvis.apply.store import ApplyStore


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    store = ApplyStore()
    cmd, args = argv[0], argv[1:]
    if cmd == "detect-ats":
        counts = runner.detect_all(store, jobops_jobs())
        print("Detected:", dict(counts.most_common()))
        print(f"Details: {store.root / 'ats_detected.md'}")
    elif cmd == "dry-run":
        llm = runner.local_llm()
        print("Local brain:", "ready" if llm else "not running (written answers will be parked)")
        for r in runner.dry_run(store, jobops_jobs(), limit=int(args[0]) if args else 5, llm=llm):
            extra = f" — waiting on: {', '.join(r.get('waiting_on', []))}" if r.get("waiting_on") else ""
            print(f"  [{r['status']:>6}] {r['company']} — {r['role']}{extra}{' — ' + r['note'] if r.get('note') else ''}")
        print(runner.summary(store))
    elif cmd == "inbox":
        items = store.inbox()
        if not items:
            print("Nothing needs you.")
        for i in sorted(items, key=lambda i: -len(i.get("waiting") or [])):
            opts = f"  options: {i['options']}" if i.get("options") else ""
            print(f"- {i['slug']}: {i['question']}  ({len(i.get('waiting') or [])} waiting){opts}")
    elif cmd == "answer" and len(args) >= 2:
        jobs = {clean(j.get("Job ID")): j for j in jobops_jobs()}
        for r in runner.answer(store, args[0], " ".join(args[1:]), jobs, runner.local_llm()):
            print(f"  re-checked: [{r['status']}] {r['company']} — {r['role']}")
        print(runner.summary(store))
    elif cmd == "status":
        print(runner.summary(store))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
