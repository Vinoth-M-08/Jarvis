"""Which application system (ATS) is behind an apply link?

First from the link itself (jobs.lever.co, *.myworkdayjobs.com, ...). For company career
pages (stripe.com/jobs/...), the page is fetched (read-only, redirects followed) and
searched for an embedded ATS (Greenhouse iframe, gh_jid, Lever / Ashby / Workday links).
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# (ats, pattern on the URL or page HTML). Order matters: specific before generic.
ATS_PATTERNS: list[tuple[str, str]] = [
    ("greenhouse", r"(?:job-boards(?:\.eu)?|boards)\.greenhouse\.io|greenhouse\.io/embed/job_app|[?&]gh_jid="),
    ("lever", r"jobs\.(?:eu\.)?lever\.co"),
    ("ashby", r"jobs\.ashbyhq\.com|ashbyhq\.com/[^\"' ]*embed"),
    ("workday", r"\.myworkdayjobs\.com|\.myworkdaysite\.com"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com"),
    ("workable", r"apply\.workable\.com|\.workable\.com/j/"),
    ("wellfound", r"wellfound\.com|angel\.co/"),
    ("ycombinator", r"ycombinator\.com/companies/[^/]+/jobs|workatastartup\.com"),
    ("linkedin", r"linkedin\.com/jobs"),
    ("icims", r"\.icims\.com"),
    ("taleo", r"\.taleo\.net"),
    ("successfactors", r"successfactors\.(?:com|eu)|\.sapsf\.com"),
    ("oracle", r"oraclecloud\.com/hcmUI|\.oraclecloud\.com/.*CandidateExperience"),
    ("bamboohr", r"\.bamboohr\.com"),
    ("recruitee", r"\.recruitee\.com"),
    ("jobvite", r"jobs\.jobvite\.com"),
    ("zoho", r"zohorecruit\.(?:com|in)"),
    ("keka", r"\.keka\.com/careers"),
    ("darwinbox", r"darwinbox\.in"),
]
EASY = {"greenhouse", "lever", "ashby", "smartrecruiters"}


@dataclass
class Detection:
    ats: str                 # e.g. "greenhouse", or "unknown" / "no-link" / "unreachable"
    how: str                 # "link" | "page" | ""
    final_url: str = ""


def from_text(text: str) -> str | None:
    for ats, pattern in ATS_PATTERNS:
        if re.search(pattern, text or "", re.IGNORECASE):
            return ats
    return None


def detect(url: str, fetch=None) -> Detection:
    """fetch(url) -> (final_url, html) — injected for tests; default uses httpx."""
    url = (url or "").strip().strip("​")
    if not url.lower().startswith(("http://", "https://")):
        return Detection("no-link", "")
    ats = from_text(url)
    if ats:
        return Detection(ats, "link", url)
    try:
        final_url, html = (fetch or _fetch)(url)
    except Exception:
        return Detection("unreachable", "", url)
    ats = from_text(final_url) or from_text(html)
    return Detection(ats, "page", final_url) if ats else Detection("unknown", "", final_url)


def greenhouse_ids(url: str, html: str = "") -> tuple[str, str] | None:
    """(board token, job id) from a Greenhouse link or a page embedding one."""
    m = re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"job_app\?for=([\w-]+)&(?:amp;)?token=(\d+)", html or "")
    if m:
        return m.group(1), m.group(2)
    gh = parse_qs(urlparse(url).query).get("gh_jid")
    if gh:
        board = re.search(r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([\w-]+)", html or "")
        if board:
            return board.group(1), gh[0]
    return None


def _fetch(url: str) -> tuple[str, str]:
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis-ATS-check"}
    with httpx.Client(follow_redirects=True, timeout=20, headers=headers) as client:
        r = client.get(url)
        return str(r.url), r.text[:500_000]
