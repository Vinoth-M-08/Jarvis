# Auto-apply — Jarvis applies to JobOps jobs for you

> Status: **Phase 1 built** (dry runs only — nothing is submitted). Source of truth for the
> jobs is the JobOps Google Sheet (via the JobOps app: `https://lambent-scone-b9805e.netlify.app`,
> `/api/jobs`). Code: `jarvis/apply/`.

## Use it (Phase 1)

```bash
.venv\Scripts\python.exe -m jarvis.apply dry-run 5          # read 5 Greenhouse forms, answer or park
.venv\Scripts\python.exe -m jarvis.apply inbox              # what Jarvis needs from you
.venv\Scripts\python.exe -m jarvis.apply answer profile-email you@example.com
.venv\Scripts\python.exe -m jarvis.apply detect-ats         # re-detect the ATS of other/blank jobs
.venv\Scripts\python.exe -m jarvis.apply status
```

Or just fill in `data/apply/profile.md` (most questions are profile fields). By voice:
*"What do you need from me?"* / *"How are my applications going?"*

**First real run (2026-10-23, 5 top-matched Greenhouse jobs):** 4 parked, 1 closed (Bitwarden,
posting gone). 16 distinct questions, mostly profile fields (name, email, phone, city,
LinkedIn, website, notice period, current / expected CTC, years of experience, resume files
for *amazon / paypal / p44*) plus a few one-offs ("Have you resigned from your current
employer?", "What is your last working date?").

**ATS re-detection of the 291 other/blank jobs:** Workday 92 · no apply link 83 · unknown 45 ·
Workable 25 · Greenhouse 14 · Y Combinator 13 · Oracle 5 · SmartRecruiters 4 ·
SuccessFactors 4 · Ashby 2 · Lever 2 · Keka 1 · iCIMS 1. So the easy group grows by 22,
Workday is really ~140 jobs, and 83 jobs can't be applied to until they have a link.
Details: `data/apply/ats_detected.md`. (Not written back to the Sheet yet — open question 2.)

**Phase 1 still to do:** write-back to the Sheet; "Needs you" list in the HUD; the same job
listed by two sources (Bitwarden is on Greenhouse *and* Wellfound) should be applied to once.

## Goal

Jarvis applies to the jobs that passed the JobOps gates, so you don't have to. It is
repeated work, so it must be **cheap** (local LLM for almost everything, Claude only for
the hard parts) and it must **never stall on you**: anything it can't answer is parked,
not asked mid-run.

## Principles

1. **Scripts fill forms; AI only thinks.** The common application systems (ATS) use the
   same form on every company's site, so a fixed script fills them. An LLM clicking
   through every form is slow, costly and fumbles.
2. **Local LLM first, Claude for hard parts.** Local (Ollama): match fields to your
   profile, pick saved answers, write short notes. Claude: unfamiliar sites, odd
   questions, anything the local model isn't confident about. Target: 80–90 % local,
   0–3 Claude requests per application.
3. **Never block on a question — park and move on.** See "Parking" below.
4. **Answer each question once, ever.** Every answer you give is saved and reused.
5. **Everything is readable Markdown** in `data/apply/` (git-ignored: it's personal).
   You can read, edit or delete any file; Jarvis picks up the change.
6. **You stay in control.** Submitting is your call (batch approval in the HUD, or
   auto-submit per source once you trust it). CAPTCHAs are handed to you, never
   bypassed. Jarvis never types passwords. At most ~20 applications a day, spread out.
7. **JobOps stays the source of truth.** Results are written back to the Sheet
   (Status, Applied At, Notes).

## Sources (from the 676 jobs in JobOps, 2026-10-23)

| ATS | Jobs | Difficulty | Approach |
|---|---|---|---|
| Greenhouse | 53 | easy | script + local LLM, no account |
| Ashby | 41 | easy | script + local LLM, no account |
| Lever | 6 | easy | script + local LLM, no account |
| SmartRecruiters | 52 | easy–medium | script; sometimes a quick sign-up |
| Wellfound | 185 | medium | one login (you); short form + note. **Their terms may forbid bots — your decision.** |
| Workday | 48 | hard | per-company accounts (you create them); long multi-page forms; Claude-assisted |
| other / blank | 291 | unknown | first re-detect the real ATS from the apply link; the rest Claude-driven or "manual" |

## Parking: questions never interrupt you

```
Queued → Filling → Ready for review → Submitted
                 ↘ Parked (needs you)  → resumes automatically once answered
                 ↘ Manual (Jarvis can't do this site) / Failed (posting closed, error)
```

- While filling, every question is answered from (in order): your **profile**, the
  **answer bank**, the local LLM (from those two), then Claude.
- If none of them is confident — or the question is sensitive (visa / work
  authorisation details, salary for an unusual case, disability / EEO, legal
  declarations, anything personal not on file) — **Jarvis does not guess**: the
  application is **parked** with the exact question, and Jarvis **moves on to the next job**.
- Questions wait in an **inbox**. Jarvis tells you at a calm moment — end of a run, or
  when you ask *"what do you need from me?"* — e.g. *"I parked 3 applications;
  2 questions need you."* Never in the middle of something.
- You answer whenever you like: by voice, in the HUD, or by editing the Markdown file.
  The answer goes into the answer bank, and **every application parked on that
  question resumes by itself**.

## Files (all in `data/apply/`, git-ignored)

```
data/apply/
  profile.md                  you: name, contacts, links, work authorisation, notice period,
                              expected CTC, years of experience, education, relocation...
  answers/<question>.md       one saved answer per file: the question (and its variants),
                              your answer, who gave it (you / derived), times used, last used
  inbox/<question>.md         open questions + which applications wait on each one
  applications/<date>_<company>_<role>.md
                              one per job: status, resume used, every field and the answer
                              given (and its source), screenshot path, why parked / failed
  screenshots/                the filled form, taken just before submitting
  log.md                      one line per run: applied / parked / failed / skipped
```

Example `answers/notice-period.md`:

```markdown
---
question: What is your notice period?
variants: [Notice period (days), When can you join?, Earliest start date]
answer: 30 days
source: you, 2026-10-24
used: 7 times, last 2026-10-26
---
```

## Phases

1. **Foundation — nothing is submitted.** Profile + answer bank + inbox files; the
   parking flow; ATS re-detection for the 291 "other"/blank jobs; write-back to the
   Sheet; a "Needs you" list and a review queue in the HUD. Dry-run on 5 Greenhouse jobs.
2. **Easy sources (~150 jobs):** Greenhouse, Ashby, Lever, SmartRecruiters. Fill →
   screenshot → review queue → you approve in batches → submit. Later: auto-submit per
   source for high-match jobs, if you choose.
3. **Wellfound (185)** — only if you accept their terms; you log in once in Jarvis's Chrome.
4. **Workday (48)** — you create the accounts (password manager / saved sessions sign in);
   Claude-assisted, slower.
5. **Everything else** — Claude works through unfamiliar sites; what it can't finish is
   marked "manual" for you.

**Daily use:** *"Jarvis, apply to today's top 10"* → runs in the background → *"8 applied,
2 parked — 1 question needs you."*

## Open questions

1. ~~Existing applier code?~~ None — built inside Jarvis (`jarvis/apply/`).
2. How should Jarvis write to the Sheet: an Apps Script web endpoint, or the Google
   Sheets API (you do the Google sign-in)?
3. ~~Inside Jarvis or JobOps?~~ Inside Jarvis.
4. Wellfound: OK to automate given their terms?
