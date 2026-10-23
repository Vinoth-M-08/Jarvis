# tests/test_apply_submit.py — phase 2 against a LOCAL mock Greenhouse form (nothing real is sent)
import shutil
from pathlib import Path

import pytest

MOCK = (Path(__file__).parent / "fixtures" / "greenhouse_mock.html").resolve().as_uri()
FIELDS = [
    {"name": "first_name", "kind": "text", "label": "First Name", "value": "Vinoth", "required": True},
    {"name": "last_name", "kind": "text", "label": "Last Name", "value": "M", "required": True},
    {"name": "email", "kind": "text", "label": "Email", "value": "me@example.com", "required": True},
    {"name": "question_1", "kind": "textarea", "label": "LinkedIn", "value": "https://linkedin.com/in/x", "required": True},
    {"name": "question_2", "kind": "yes_no", "label": "Have you resigned?", "value": "No", "required": True},
    {"name": "question_3[]", "kind": "multi_choice", "label": "Pay", "value": "11,00,000 to 15,00,000 per year",
     "required": True},
]


def _chrome_ok():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return any(Path(p).exists() for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                           r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")) \
        or shutil.which("google-chrome") is not None


pytestmark = pytest.mark.skipif(not _chrome_ok(), reason="needs Playwright + Google Chrome")


@pytest.fixture
def form(tmp_path):
    from jarvis.apply.greenhouse_form import GreenhouseForm
    f = GreenhouseForm(tmp_path / "profile", headless=True, slow_mo_ms=0)
    yield f
    f.close()


@pytest.fixture
def resume(tmp_path):
    p = tmp_path / "Vinoth_Resume.pdf"
    p.write_bytes(b"%PDF-1.4 mock")
    return str(p)


def test_fill_every_field_type_and_submit(form, resume, tmp_path):
    page = form.open(MOCK)
    result = form.fill(page, FIELDS + [{"name": "resume", "kind": "file", "label": "Resume", "value": resume,
                                        "required": True}])
    assert result.problems == [] and len(result.filled) == 7
    shot = form.screenshot(page, tmp_path / "filled.png")
    assert Path(shot).stat().st_size > 1000
    out = form.submit(page, wait_s=10)
    assert out.status == "submitted", out
    sent = page.evaluate("window.__submitted")
    assert sent == {"email": "me@example.com", "resigned": "No", "pay": "11,00,000 to 15,00,000 per year",
                    "resume": "Vinoth_Resume.pdf"}


def test_security_code_is_handed_to_you(form, resume):
    page = form.open(MOCK + "#code")
    form.fill(page, FIELDS + [{"name": "resume", "kind": "file", "label": "Resume", "value": resume}])
    assert form.submit(page, wait_s=10).status == "code_needed"


def test_missing_answer_reports_errors_not_success(form):
    page = form.open(MOCK)
    form.fill(page, FIELDS[:2])
    out = form.submit(page, wait_s=8)
    assert out.status == "errors" and "required" in out.message


def test_option_not_offered_is_a_problem(form):
    page = form.open(MOCK)
    bad = dict(FIELDS[4], value="Maybe")
    assert form.fill(page, [bad]).problems == ["Have you resigned?: the option didn't take"]


def test_submitter_end_to_end_with_approval(tmp_path, resume, monkeypatch):
    """Approve -> worker fills the (mock) form, screenshots, submits, records; then nothing re-sends it."""
    import threading
    from jarvis.apply.greenhouse_form import GreenhouseForm
    from jarvis.apply.service import ApplyService
    said, done = [], threading.Event()
    svc = ApplyService(tmp_path / "apply", llm=lambda: None, notify=lambda t: (said.append(t), done.set()),
                       form_factory=lambda: GreenhouseForm(tmp_path / "profile", headless=True, slow_mo_ms=0),
                       delay_s=(0, 0))
    meta = {"job_id": "greenhouse:mockco:1", "company": "Mockco", "role": "Software Engineer", "status": "ready",
            "form_url": MOCK, "link": MOCK,
            "fields": FIELDS + [{"name": "resume", "kind": "file", "label": "Resume", "value": resume}]}
    svc.store.write_application(meta["job_id"], meta, [("First Name *", "Vinoth", "profile:first_name")])
    svc._recheck = lambda m: svc.store.application(m["job_id"])      # no network: the record is the form
    svc.submitter._recheck = svc._recheck
    assert svc.approve() == ["greenhouse:mockco:1"]
    assert done.wait(60), "submitter didn't finish"
    rec = svc.store.application("greenhouse:mockco:1")
    assert rec["status"] == "submitted" and rec["submitted_at"] and Path(rec["screenshot"]).exists()
    assert said[-1].startswith("Submitted 1: Mockco.")
    assert svc.approve() == []                                        # already sent: nothing to approve
    assert "| First Name * | Vinoth |" in (svc.store.root / "applications" / "greenhouse-mockco-1.md").read_text(
        encoding="utf-8")
    svc.submitter.shutdown()
