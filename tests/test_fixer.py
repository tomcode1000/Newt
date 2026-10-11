"""Fixer + verifier (gate check c). Scripted model, real Docker sandboxes."""

import pytest
from fakes import FakeClient
from test_coordinator import CHANGELOG, broken_script, make_docs, pass_script

from newt import fixer
from newt.coordinator import Coordinator
from newt.ledger import Ledger
from newt.sandbox import docker_available
from newt.worker import Worker

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")

GOOD_FIX = "# a.md\n1. Run the example (FIXED: uses json.dumps)\n"
BAD_FIX = "# a.md\n1. Run the example (still wrong)\n"


def factory(page_text, page_name, **kw):
    """First run of a page: broken. Verification run: passes only if the page carries the good fix."""
    kw["client"] = FakeClient(pass_script() if "FIXED" in page_text else broken_script())
    kw["fetcher"] = lambda url: CHANGELOG
    return Worker(page_text, page_name, **kw)


def run(tmp_path, monkeypatch, fixer_turns, gate=True):
    monkeypatch.setattr(fixer, "FIXES_DIR", tmp_path / "fixes")
    (tmp_path / "docs").mkdir()
    docs = make_docs(tmp_path / "docs", ["a.md"])
    client = FakeClient(fixer_turns)
    ledger = Ledger()
    report = Coordinator(docs, ledger=ledger, workers=1, gate=gate, fix=True, client=client, model="fake/model",
                         worker_factory=factory).run()
    return report, ledger, client


def test_verified_fix_is_sent(tmp_path, monkeypatch):
    report, ledger, client = run(tmp_path, monkeypatch, [[("write_page", {"content": GOOD_FIX, "changes": "dumps2 -> dumps"})]])
    [cause] = report.causes
    assert cause.method == "single" and cause.pages == ["a.md"]
    [outcome] = report.fixes
    assert outcome.state == "verified" and outcome.sent and outcome.verify_status == "pass"
    gate = report.results["a.md"].gate
    assert gate.fixed is True and gate.action == "send_fix"
    assert any(r.startswith("fixed: re-run from zero passed") for r in gate.reasons)
    assert (tmp_path / "fixes" / f"run-{report.run_id}" / "a.md").read_text() == GOOD_FIX
    [row] = ledger.fixes(report.run_id)
    assert row["state"] == "verified" and row["sent"] == 1 and row["fixed_text"] == GOOD_FIX
    assert {p["name"]: p["gate_action"] for p in ledger.pages(report.run_id)} == {"a.md": "send_fix"}
    # The fixer saw the page, the evidence and the fetched source text.
    prompt = client.calls[0]["messages"][1]["content"]
    assert "<page>" in prompt and "dumps2" in prompt and "<source" in prompt


def test_fix_that_fails_its_rerun_is_not_sent_with_gate_on(tmp_path, monkeypatch):
    report, ledger, _ = run(tmp_path, monkeypatch, [[("write_page", {"content": BAD_FIX, "changes": "?"})]])
    [outcome] = report.fixes
    assert outcome.state == "failed" and not outcome.sent
    assert report.results["a.md"].gate.action == "write_fix"
    assert report.results["a.md"].gate.fixed is False
    assert ledger.fixes(report.run_id)[0]["sent"] == 0


def test_baseline_sends_the_failed_fix_anyway(tmp_path, monkeypatch):
    report, ledger, _ = run(tmp_path, monkeypatch, [[("write_page", {"content": BAD_FIX, "changes": "?"})]], gate=False)
    [outcome] = report.fixes
    assert outcome.state == "failed" and outcome.sent
    assert report.results["a.md"].gate.action == "send_fix"


def test_unusable_fixer_answer_is_an_error_not_a_fix(tmp_path, monkeypatch):
    unchanged = "# a.md\n1. Run the example\n"
    report, ledger, client = run(tmp_path, monkeypatch, [[("write_page", {"content": unchanged, "changes": ""})]] * 2)
    [outcome] = report.fixes
    assert outcome.state == "error" and not outcome.sent
    assert len(client.calls) == 2  # one retry, then give up
    assert ledger.fixes(report.run_id)[0]["state"] == "error"
