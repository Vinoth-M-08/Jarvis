"""Answer an application question from what we know — or park it (docs/AUTO_APPLY.md).

Order: profile -> saved answer (exact / near-identical wording) -> saved answer the local
LLM says means the same -> local LLM draft for written questions -> park. Sensitive
questions (salary, visa, demographics, declarations...) are never guessed or drafted.
Optional questions with no answer are left blank, never parked.
"""
from __future__ import annotations
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from jarvis.apply.store import ApplyStore, SavedAnswer, slugify


@dataclass
class Question:
    label: str
    required: bool = False
    kind: str = "text"                  # text | textarea | choice | multi_choice | file | yes_no
    options: list[str] = field(default_factory=list)
    field_name: str = ""                # the form's own field name (first_name, question_123...)


@dataclass
class Resolution:
    answer: str | None                  # None = left blank (optional) or parked
    source: str                         # profile:email | saved:<slug> | draft (local LLM) | blank | parked
    park: tuple[str, str] | None = None  # (inbox slug, kind) when parked


# Question wording -> profile field. First match wins (so "preferred first name" beats "first name").
PROFILE_RULES: list[tuple[str, str]] = [
    (r"\bpreferred (?:first )?name|\bnick ?name", "preferred_name"),
    (r"\bfirst name|\bgiven name", "first_name"),
    (r"\blast name|\bsurname|\bfamily name", "last_name"),
    (r"\be-?mail", "email"),
    (r"\bphone|\bmobile|\bcontact number", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"portfolio|personal (?:web)?site|\bwebsite\b", "portfolio"),
    (r"current(?:ly)? (?:located|location|city)|where are you (?:located|based)|\bcity\b|^location$", "city"),
    (r"\bcountry\b", "country"),
    (r"(?:current|present) (?:company|employer)(?: name)?$|^(?:current )?employer(?: name)?$|^company name$", "current_company"),
    (r"current (?:job )?(?:title|role|designation)", "current_title"),
    (r"years of (?:professional |relevant |work )?experience|how many years", "years_experience"),
    (r"notice period|when (?:can|could) you (?:start|join)|available to (?:start|join)|earliest (?:start|joining)|"
     r"joining date|start date|availability to start", "notice_period"),
    (r"current (?:fixed |total |annual |base |gross )*(?:ctc|salary|compensation|pay)", "current_ctc"),
    (r"expected (?:fixed |total |annual |base |gross )*(?:ctc|salary|compensation)|salary expectation|"
     r"desired (?:annual )?(?:salary|compensation)|compensation expectation", "expected_ctc"),
    (r"sponsor", "needs_sponsorship"),
    (r"authori[sz]ed to work|work authori[sz]ation|legally (?:eligible|authori[sz]ed)|right to work", "work_authorization"),
    (r"relocat", "willing_to_relocate"),
    (r"highest (?:degree|education|qualification)|\bdegree\b", "highest_degree"),
    (r"university|college|school", "university"),
    (r"graduat(?:ion|ed) (?:year|date)|year of graduation", "graduation_year"),
]

# Never guessed or drafted by an LLM: only your profile / your saved answers.
SENSITIVE = re.compile(
    r"salary|ctc|compensation|sponsor|visa|authori[sz]|citizen|gender|pronoun|race|ethnic|hispanic|"
    r"veteran|disab|sexual|orientation|religio|criminal|convict|background check|drug test|"
    r"i (?:certify|confirm|acknowledge|agree|consent)|declar|terms|privacy|date of birth|\bage\b|"
    r"marital|caste|aadhaar|pan\b|passport|social security|ssn", re.IGNORECASE)

# Written answers the local LLM may draft (you review them before anything is sent).
WRITING = re.compile(r"\bwhy\b|tell us|describe|cover letter|about yourself|motivat|interest(?:ed|s) you|"
                     r"anything else|additional information|what excites", re.IGNORECASE)

YES_NO_KEYS = {"needs_sponsorship", "work_authorization", "willing_to_relocate"}
YES_WORDS = {"yes", "y", "true", "yeah"}
NO_WORDS = {"no", "n", "false", "nope"}


def normalize(label: str) -> str:
    t = re.sub(r"[*:?!.()\[\]]", " ", label.lower())
    t = re.sub(r"\b(?:please|kindly|enter|provide|your)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Resolver:
    def __init__(self, store: ApplyStore, llm: Callable[[str], str] | None = None):
        """llm(prompt) -> text: the local brain (None = no LLM steps)."""
        self.store = store
        self.llm = llm
        self.profile = store.profile()
        self.saved = store.answers()

    def resolve(self, q: Question, job: dict) -> Resolution:
        if q.kind == "file":
            return self._file(q, job)

        # Your answer to this exact wording beats the profile (e.g. you picked one of this
        # form's options after the profile value didn't fit them).
        saved = self._saved_match(q.label, use_llm=False)
        if saved:
            self.store.mark_used(saved.slug)
            return self._fit(q, saved.answer, f"saved:{saved.slug}")

        key = self._profile_key(q.label)
        if key:
            value = self.profile.get(key)
            if value not in (None, ""):
                return self._fit(q, str(value), f"profile:{key}")
            if q.required:
                return Resolution(None, "parked", (f"profile-{key}", f"profile:{key}"))
            return Resolution(None, "blank")

        saved = self._saved_match(q.label, use_llm=True)
        if saved:
            self.store.mark_used(saved.slug)
            return self._fit(q, saved.answer, f"saved:{saved.slug}")

        if not q.required:
            return Resolution(None, "blank")
        sensitive = bool(SENSITIVE.search(q.label))
        if not sensitive and self.llm and WRITING.search(q.label) and q.kind in ("text", "textarea"):
            draft = self._draft(q, job)
            if draft:
                return Resolution(draft, "draft (local LLM)")
        return Resolution(None, "parked", (slugify(q.label), "answer"))

    # ─── steps ───
    @staticmethod
    def _profile_key(label: str) -> str | None:
        low = label.lower().strip(" *:?")
        # "Have you resigned from your current employer?" is a yes/no question, not "current company":
        # yes/no questions only map to the yes/no profile fields.
        yes_no = re.match(r"(?:have|are|do|did|will|would|can|could|is|were|has) (?:you|your)\b", low)
        for pattern, key in PROFILE_RULES:
            if re.search(pattern, low):
                return key if not yes_no or key in YES_NO_KEYS else None
        return None

    def _saved_match(self, label: str, use_llm: bool) -> SavedAnswer | None:
        n = normalize(label)
        best, best_ratio = None, 0.0
        for s in self.saved:
            for text in [s.question, *s.variants]:
                r = difflib.SequenceMatcher(None, n, normalize(text)).ratio()
                if r > best_ratio:
                    best, best_ratio = s, r
        if best_ratio >= 0.88:
            return best
        if use_llm and self.llm and self.saved and not SENSITIVE.search(label):
            return self._llm_match(label)
        return None

    def _llm_match(self, label: str) -> SavedAnswer | None:
        """Ask the local brain whether a saved question means the same thing (cheap, local)."""
        menu = "\n".join(f"{i + 1}. {s.question}" for i, s in enumerate(self.saved))
        prompt = (f"A job application asks: \"{label}\"\n\nSaved questions:\n{menu}\n\n"
                  "Which saved question asks for exactly the same information? Reply with only its "
                  "number, or 0 if none of them does.")
        try:
            reply = self.llm(prompt)
        except Exception:
            return None
        m = re.search(r"\d+", reply or "")
        i = int(m.group()) if m else 0
        if 1 <= i <= len(self.saved):
            s = self.saved[i - 1]
            self.store.save_answer(s.question, s.answer, source=s.source.split(",")[0], variants=[label])
            return s
        return None

    def _draft(self, q: Question, job: dict) -> str | None:
        facts = {k: v for k, v in self.profile.items() if k != "resumes" and v not in (None, "")}
        prompt = (f"Write a short, honest answer (2-4 sentences, first person, plain text) to this job "
                  f"application question.\nQuestion: {q.label}\nCompany: {job.get('Company Name')}\n"
                  f"Role: {job.get('Role')}\nWhy it fits (from the job matcher): {job.get('Job Match', '')}\n"
                  f"My details: {facts}\nOnly use facts given here; don't invent employers, numbers or degrees.")
        try:
            text = (self.llm(prompt) or "").strip()
        except Exception:
            return None
        return text or None

    def _fit(self, q: Question, value: str, source: str) -> Resolution:
        """Make a known answer fit the question's options (choice / yes-no). If none fits, this
        exact wording is parked: your pick is saved for it and used from then on."""
        if q.kind not in ("choice", "multi_choice", "yes_no") or not q.options:
            return Resolution(value, source)
        option = match_option(value, q.options)
        if option is not None:
            return Resolution(option, source)
        if not q.required:
            return Resolution(None, "blank")
        return Resolution(None, "parked", (slugify(q.label), "answer"))

    def _file(self, q: Question, job: dict) -> Resolution:
        low = q.label.lower()
        if "cover" in low:
            return Resolution(None, "blank") if not q.required else Resolution(None, "parked", ("cover-letter-file", "answer"))
        name = (job.get("Resume") or "").strip() or "default"
        path = (self.profile.get("resumes") or {}).get(name)
        if path and Path(str(path)).is_file():
            return Resolution(str(path), f"profile:resumes.{name}")
        return Resolution(None, "parked", (f"resume-{slugify(name)}", f"profile:resumes.{name}"))


def match_option(value: str, options: list[str]) -> str | None:
    """'yes' -> 'Yes, I am'; 'Bachelor' -> "Bachelor's Degree"; None if nothing fits."""
    v = normalize(value)
    norm = {o: normalize(o) for o in options}
    for o, n in norm.items():
        if n == v:
            return o
    first = v.split(" ")[0] if v else ""
    if first in YES_WORDS | NO_WORDS:
        want = "yes" if first in YES_WORDS else "no"
        hits = [o for o, n in norm.items() if n.split(" ")[0] == want]
        if len(hits) == 1:
            return hits[0]
    hits = [o for o, n in norm.items() if v and (v in n or n in v)]
    if len(hits) == 1:
        return hits[0]
    best = max(options, key=lambda o: difflib.SequenceMatcher(None, v, norm[o]).ratio(), default=None)
    if best is not None and difflib.SequenceMatcher(None, v, norm[best]).ratio() >= 0.85:
        return best
    return None
