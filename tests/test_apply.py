# tests/test_apply.py — auto-apply phase 1 (nothing is submitted)
import pytest

from jarvis.apply import ats, runner
from jarvis.apply.resolver import Question, Resolver, match_option
from jarvis.apply.store import ApplyStore, read_doc

JOB = {"Job ID": "greenhouse:acme:123", "Company Name": "Acme", "Role": "Software Engineer",
       "Apply Link": "https://job-boards.greenhouse.io/acme/jobs/123", "ATS": "greenhouse",
       "Gate Result": "Pass", "Status": "Not Applied", "Job Match": "91 - strong", "Resume": "amazon"}
FORM = [Question("First Name", True), Question("Email", True), Question("Phone", False),
        Question("Resume/CV", True, "file"),
        Question("Which computer type do you use; Mac, Linux or PC?", True),
        Question("Are you willing to relocate?", True, "choice", ["Yes, within India", "Yes, anywhere", "No"]),
        Question("Gender", False, "choice", ["Male", "Female", "Decline to self-identify"])]


@pytest.fixture
def store(tmp_path):
    s = ApplyStore(tmp_path / "apply")
    s.set_profile("first_name", "Vinoth")
    s.set_profile("email", "me@example.com")
    s.set_profile("willing_to_relocate", "yes")
    resume = tmp_path / "amazon.pdf"
    resume.write_bytes(b"%PDF-1.4")
    s.set_profile("resumes.amazon", str(resume))
    return s


@pytest.fixture
def fake_form(monkeypatch):
    monkeypatch.setattr(runner, "greenhouse_questions", lambda board, job: (list(FORM), {}))


def test_profile_edits_keep_comments_and_layout(store):
    text = store.profile_path.read_text(encoding="utf-8")
    assert "# e.g. \"Indian citizen" in text and "first_name: Vinoth" in text
    assert store.profile()["resumes"]["amazon"].endswith("amazon.pdf")
    assert store.profile()["resumes"]["paypal"] is None


def test_unanswerable_question_parks_the_application_and_moves_on(store, fake_form):
    app = runner.check_application(store, Resolver(store), JOB)
    assert app["status"] == "parked"
    slugs = {i["slug"]: i for i in store.inbox()}
    assert "which-computer-type-do-you-use-mac-linux-or-pc" in slugs           # custom question
    assert "are-you-willing-to-relocate" in slugs                               # "yes" fits 2 options
    assert slugs["are-you-willing-to-relocate"]["options"] == ["Yes, within India", "Yes, anywhere", "No"]
    assert not any("phone" in s or "gender" in s for s in slugs)                # optional: left blank
    body = (store.root / "applications" / "greenhouse-acme-123.md").read_text(encoding="utf-8")
    assert "| First Name * | Vinoth | profile:first_name |" in body
    assert "amazon.pdf" in body and "— waiting for you —" in body


def test_answering_once_resumes_every_waiting_application(store, fake_form):
    other = dict(JOB, **{"Job ID": "greenhouse:beta:9", "Apply Link": "https://job-boards.greenhouse.io/beta/jobs/9"})
    for job in (JOB, other):
        runner.check_application(store, Resolver(store), job)
    item = next(i for i in store.inbox() if i["slug"].startswith("which-computer"))
    assert len(item["waiting"]) == 2
    runner.answer(store, item["slug"], "PC (Windows)", jobs_by_id={JOB["Job ID"]: JOB, other["Job ID"]: other})
    still = runner.answer(store, "are-you-willing-to-relocate", "Yes, within India",
                          jobs_by_id={JOB["Job ID"]: JOB, other["Job ID"]: other})
    assert [a["status"] for a in still] == ["ready", "ready"]
    assert store.inbox() == []
    assert {a.answer for a in store.answers()} == {"PC (Windows)", "Yes, within India"}


def test_sensitive_questions_are_never_drafted(store):
    drafts = []
    r = Resolver(store, llm=lambda p: drafts.append(p) or "A lovely answer.")
    salary = r.resolve(Question("What are your salary expectations?", True, "textarea"), JOB)
    why = r.resolve(Question("Why do you want to work at Acme?", True, "textarea"), JOB)
    assert salary.park and salary.answer is None                  # profile has no expected_ctc
    assert why.source == "draft (local LLM)" and why.answer == "A lovely answer."
    assert len(drafts) == 1 and "salary" not in drafts[0].lower()


def test_local_llm_matches_a_reworded_question(store):
    store.save_answer("What is your notice period?", "30 days")
    r = Resolver(store, llm=lambda p: "1")
    res = r.resolve(Question("How soon could you join us if selected?", True), JOB)
    assert res.answer == "30 days" and res.source.startswith("saved:")
    saved = store.answers()[0]
    assert "How soon could you join us if selected?" in saved.variants      # learned for next time


def test_missing_resume_file_parks_with_the_resume_name(store):
    r = Resolver(store).resolve(Question("Resume/CV", True, "file"), dict(JOB, Resume="paypal"))
    assert r.park == ("resume-paypal", "profile:resumes.paypal")


def test_answering_a_profile_question_fills_the_profile(store, fake_form):
    store.set_profile("email", "")
    runner.check_application(store, Resolver(store), JOB)
    runner.answer(store, "profile-email", "new@example.com", jobs_by_id={JOB["Job ID"]: JOB})
    assert store.profile()["email"] == "new@example.com"
    assert "# Profile" in store.profile_path.read_text(encoding="utf-8")


def test_match_option():
    assert match_option("yes", ["Yes", "No"]) == "Yes"
    assert match_option("Bachelor", ["High School", "Bachelor's Degree", "Master's Degree"]) == "Bachelor's Degree"
    assert match_option("yes", ["Yes, within India", "Yes, anywhere", "No"]) is None   # ambiguous: ask
    assert match_option("no", ["Yes, within India", "Yes, anywhere", "No"]) == "No"


def test_ats_detection_from_link_and_page():
    assert ats.detect("https://mastercard.wd1.myworkdayjobs.com/x").ats == "workday"
    assert ats.detect("https://apply.workable.com/acme/j/ABC/").ats == "workable"
    assert ats.detect("").ats == "no-link"
    page = lambda url: (url, '<iframe src="https://boards.greenhouse.io/embed/job_app?for=stripe&token=12345">')
    d = ats.detect("https://stripe.com/jobs/listing/x/12345", fetch=page)
    assert (d.ats, d.how) == ("greenhouse", "page")
    assert ats.greenhouse_ids("https://stripe.com/jobs/12345", page("")[1]) == ("stripe", "12345")
    assert ats.detect("https://example.com/careers", fetch=lambda u: (u, "<html>apply here</html>")).ats == "unknown"
    assert ats.detect("https://down.example", fetch=lambda u: (_ for _ in ()).throw(OSError())).ats == "unreachable"


def test_summary_says_what_needs_you(store, fake_form):
    runner.check_application(store, Resolver(store), JOB)
    text = runner.summary(store)
    assert text.startswith("Applications: 0 ready, 1 parked.") and "need you" in text


@pytest.mark.parametrize("label,key", [
    ("Current Fixed CTC", "current_ctc"), ("Expected Fixed CTC", "expected_ctc"),
    ("What is your current total annual compensation?", "current_ctc"), ("Location", "city"),
    ("When are you available to start working on a full-time basis?", "notice_period"),
    ("Official Notice Period in Days", "notice_period"), ("Current Company", "current_company"),
    ("Have you resigned from your current employer?", None),          # yes/no question, not a company
    ("Are you willing to relocate?", "willing_to_relocate"),
    ("Will you now or in the future require visa sponsorship?", "needs_sponsorship"),
])
def test_profile_mapping_from_real_forms(label, key):
    assert Resolver._profile_key(label) == key


def test_greenhouse_ids_from_company_career_page():
    link = "https://bitwarden.com/careers/7872214003/?gh_jid=7872214003"
    no_board_in_page = lambda u: (u, "<html>no board here</html>")
    assert runner._greenhouse_ids(link, fetch=no_board_in_page) == ("bitwarden", "7872214003")
    embedded = lambda u: (u, '<script src="https://boards.greenhouse.io/embed/job_board?for=bitwardeninc"></script>')
    assert runner._greenhouse_ids(link, fetch=embedded) == ("bitwardeninc", "7872214003")


# --- service: dashboard + voice ---

def test_pick_option_from_speech():
    from jarvis.apply.service import pick_option
    opts = ["Immediately", "Within 1 week", "Within 2 weeks", "Within 1 month"]
    assert pick_option("the second one", opts) == "Within 1 week"
    assert pick_option("option 4", opts) == "Within 1 month"
    assert pick_option("last", opts) == "Within 1 month"
    assert pick_option("within 1 month", opts) == "Within 1 month"     # not "option one"
    assert pick_option("immediately.", opts) == "Immediately"
    assert pick_option("maybe next year", opts) is None


def test_normalize_spoken_email_and_phone():
    from jarvis.apply.service import normalize_spoken
    assert normalize_spoken("profile:email", "Gopi R 525 at gmail dot com") == "gopir525@gmail.com"
    assert normalize_spoken("profile:phone", "+91 98765 43210.") == "+919876543210"
    assert normalize_spoken("profile:city", "Chennai.") == "Chennai"


def test_speakable_reads_options():
    from jarvis.apply.service import speakable
    assert speakable({"question": "Email", "kind": "profile:email"}) == "What's your email?"
    s = speakable({"question": "Have you resigned?", "kind": "answer", "options": ["Yes", "No"]})
    assert s == "Have you resigned? The options are: Yes, or No."


def test_find_resume_files(tmp_path):
    from jarvis.apply.service import find_resume_files
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "Vinoth_PayPal_Resume.pdf").write_bytes(b"%PDF")
    (tmp_path / "Resume_general.pdf").write_bytes(b"%PDF")
    (tmp_path / "invoice.pdf").write_bytes(b"%PDF")
    found = find_resume_files("paypal", roots=[tmp_path])
    assert found[0].endswith("Vinoth_PayPal_Resume.pdf") and not any("invoice" in f for f in found)


def test_service_answers_and_snapshot(tmp_path, monkeypatch):
    from jarvis.apply.service import ApplyService
    monkeypatch.setattr(runner, "greenhouse_questions", lambda board, job: (list(FORM), {}))
    changes = []
    svc = ApplyService(tmp_path / "apply", llm=lambda: None, on_change=lambda: changes.append(1))
    svc.store.set_profile("willing_to_relocate", "yes")
    runner.check_application(svc.store, Resolver(svc.store), JOB)
    snap = svc.snapshot()
    assert snap["counts"]["parked"] == 1 and snap["inbox"][0]["waiting_count"] == 1
    email = next(i for i in snap["inbox"] if i["slug"] == "profile-email")
    assert email["input"] == "text"
    done = svc.answer("profile-email", "me at example dot com")
    assert done["answer"] == "me@example.com" and svc.store.profile()["email"] == "me@example.com"
    with pytest.raises(ValueError):
        svc.answer("are-you-willing-to-relocate", "maybe on Tuesdays")    # not one of the options
    assert svc.answer("are-you-willing-to-relocate", "the second one")["answer"] == "Yes, anywhere"
    assert changes and svc.details(JOB["Job ID"])["rows"][0]["question"].startswith("First Name")


def test_background_run_reports_when_done(tmp_path, monkeypatch):
    import threading
    from jarvis.apply.service import ApplyService
    monkeypatch.setattr(runner, "greenhouse_questions", lambda board, job: (list(FORM), {}))
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    said, done = [], threading.Event()
    svc = ApplyService(tmp_path / "apply", llm=lambda: None, jobs=lambda: [JOB],
                       notify=lambda text: (said.append(text), done.set()))
    assert svc.run_in_background(5)
    assert done.wait(10)
    assert said and said[0].startswith("Job check done: 1 checked — 0 ready, 1 parked.")
    assert "ask me your questions" in said[0]
