"""Coordinator + ledger: parallel pages, root-cause grouping, honest stop on the daily limit.
Scripted fake model, real Docker sandboxes."""

import pytest
from fakes import FakeClient, report

from newt import llm
from newt.coordinator import Coordinator, run_summary
from newt.ledger import Ledger
from newt.sandbox import docker_available
from newt.worker import Worker

docker_only = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")

CHANGELOG_URL = "https://example.test/changelog"
CHANGELOG = "## 2.0\n- `json.dumps2` was removed; use `json.dumps` instead.\n"
BROKEN = "python -c 'import json; json.dumps2({})'"


def broken_script():
    return [
        [("run", {"command": BROKEN, "step": 1, "purpose": "follow"})],
        [("fetch", {"url": CHANGELOG_URL})],
        [report("docs_broken", failing_step={"index": 1, "text": "Run the example"},
                evidence={"command": BROKEN, "output_tail": "AttributeError: module 'json' has no attribute 'dumps2'"},
                cause="json.dumps2 was removed in 2.0", cause_source=CHANGELOG_URL,
                cause_quote="`json.dumps2` was removed", suggested_fix="Use json.dumps")],
    ]


def pass_script():
    return [[("run", {"command": "echo hi", "step": 1, "purpose": "follow"})], [report("pass")]]


def make_docs(tmp_path, names):
    for n in names:
        (tmp_path / n).write_text(f"# {n}\n1. Run the example\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("not a page", encoding="utf-8")
    return tmp_path


def scripted_factory(scripts):
    """Each page gets its own scripted client, whatever client the coordinator passes."""

    def factory(page_text, page_name, **kw):
        kw["client"] = FakeClient(scripts[page_name]())
        kw["fetcher"] = lambda url: CHANGELOG
        return Worker(page_text, page_name, **kw)

    return factory


@docker_only
def test_parallel_run_groups_shared_cause(tmp_path):
    docs = make_docs(tmp_path, ["a.md", "b.md", "c.md"])
    scripts = {"a.md": broken_script, "b.md": broken_script, "c.md": pass_script}
    group_client = FakeClient([[("group_causes", {"groups": [{"summary": "json.dumps2 removed in 2.0", "pages": ["a.md", "b.md"]}]})]])
    ledger = Ledger()
    report_ = Coordinator(docs, ledger=ledger, workers=3, fix=False, client=group_client, model="fake/model",
                          worker_factory=scripted_factory(scripts)).run()

    assert sorted(report_.results) == ["a.md", "b.md", "c.md"]  # README skipped
    assert report_.results["c.md"].verdict.status == "pass"
    assert [c.pages for c in report_.causes] == [["a.md", "b.md"]]
    assert report_.causes[0].method == "model" and report_.causes[0].source == CHANGELOG_URL
    # The grouping call saw both failures with their error signatures.
    sent = group_client.calls[0]["messages"][1]["content"]
    assert "a.md" in sent and "dumps2" in sent

    summary = run_summary(ledger, report_.run_id)
    assert summary["run"]["status"] == "done"
    assert {p["page"]: p["gate_action"] for p in summary["pages"]} == {"a.md": "write_fix", "b.md": "write_fix", "c.md": "none"}
    assert summary["causes"][0]["pages"] == ["a.md", "b.md"]
    assert any("cause 1" in e["message"] for e in ledger.events(report_.run_id))


@docker_only
def test_bad_grouping_answer_falls_back_to_error_signature(tmp_path):
    docs = make_docs(tmp_path, ["a.md", "b.md"])
    # The model leaves b.md out: not a valid grouping.
    group_client = FakeClient([[("group_causes", {"groups": [{"summary": "x", "pages": ["a.md"]}]})]])
    report_ = Coordinator(docs, ledger=Ledger(), workers=2, fix=False, client=group_client, model="fake/model",
                          worker_factory=scripted_factory({"a.md": broken_script, "b.md": broken_script})).run()
    assert [c.pages for c in report_.causes] == [["a.md", "b.md"]]
    assert report_.causes[0].method == "signature"


@docker_only
def test_daily_limit_stops_the_run_and_marks_the_rest_not_run(tmp_path):
    docs = make_docs(tmp_path, ["a.md", "b.md", "c.md"])

    class Limited:
        def __init__(self):
            from types import SimpleNamespace
            self.chat = SimpleNamespace(send=self.send)

        def send(self, **kw):
            raise llm.DailyLimitReached("OpenRouter daily free-model limit reached")

    def factory(page_text, page_name, **kw):
        kw["client"] = FakeClient(pass_script()) if page_name == "a.md" else Limited()
        return Worker(page_text, page_name, **kw)

    ledger = Ledger()
    report_ = Coordinator(docs, ledger=ledger, workers=1, fix=False, client=FakeClient([]), model="fake/model",
                          worker_factory=factory).run()
    assert report_.results["a.md"].verdict.status == "pass"
    assert sorted(report_.not_run) == ["b.md", "c.md"]
    assert "daily" in report_.stopped
    assert ledger.run(report_.run_id)["status"] == "stopped"
    assert {p["name"]: p["state"] for p in ledger.pages(report_.run_id)} == {"a.md": "done", "b.md": "not_run", "c.md": "not_run"}


def test_ledger_round_trip(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    run_id = ledger.start_run("docs", "m", True)
    ledger.add_page(run_id, "p.md", "docs/p.md")
    ledger.add_cause(run_id, 1, "rename", ["p.md"], "u", "q", "single")
    ledger.add_fix(run_id, 1, "p.md")
    ledger.update_fix(run_id, "p.md", state="verified", sent=1)
    ledger.finish_run(run_id)
    again = Ledger(tmp_path / "l.db")
    assert again.causes(run_id)[0]["pages"] == ["p.md"]
    assert again.fixes(run_id)[0]["state"] == "verified"
    assert again.run(run_id)["status"] == "done"
