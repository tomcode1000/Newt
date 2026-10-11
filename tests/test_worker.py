"""Worker loop + docs-fault gate, with a scripted fake model and real Docker sandboxes."""

import json
from types import SimpleNamespace

import pytest

from newt import llm
from newt.sandbox import docker_available
from newt.worker import Verdict, Worker, _explained, _signature

docker_only = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")

PAGE = """# Quickstart
1. Create `hello.py` containing `import json; print(json.dumps({"ok": True}))`
2. Run `python hello.py`
"""


from fakes import FakeClient, report, tool_results  # noqa: E402,F401


BROKEN_STEP = "python -c 'import json; json.dumps2({})'"
CHANGELOG_URL = "https://example.test/changelog"
CHANGELOG = "## 2.0\n- `json.dumps2` was removed; use `json.dumps` instead.\n"


def broken_run(quote="`json.dumps2` was removed", fetch=True):
    turns = [
        [("run", {"command": "mkdir -p proj && cd proj", "step": 1, "purpose": "follow"})],
        [("run", {"command": BROKEN_STEP, "step": 2, "purpose": "follow"})],
    ]
    if fetch:
        turns.append([("fetch", {"url": CHANGELOG_URL})])
    turns.append([report(
        "docs_broken",
        failing_step={"index": 2, "text": "Run the dumps2 example"},
        evidence={"command": BROKEN_STEP, "output_tail": "AttributeError"},
        cause="dumps2 was removed in 2.0", cause_source=CHANGELOG_URL if fetch else "",
        cause_quote=quote if fetch else "", suggested_fix="Use json.dumps",
    )])
    return turns


def make_worker(turns, **kw):
    return Worker(PAGE, "quickstart.md", client=FakeClient(turns), fetcher=lambda url: CHANGELOG, **kw)


@docker_only
def test_pass_run_records_actions_and_uses_one_sandbox():
    turns = [
        [("write_file", {"path": "hello.py", "content": 'import json; print(json.dumps({"ok": True}))\n', "step": 1, "purpose": "follow"})],
        [("run", {"command": "python hello.py", "step": 2, "purpose": "follow"})],
        [report("pass")],
    ]
    w = make_worker(turns)
    result = w.run()
    assert result.verdict.status == "pass"
    assert result.gate.action == "none"
    assert [a.kind for a in result.actions] == ["write_file", "run"]
    assert '{"ok": true}' in result.actions[1].output_tail
    assert result.turns == 3
    # The tool result for the run went back to the model with the output.
    last = w.client.calls[-1]["messages"][-1]
    assert last["role"] == "tool" and '{"ok": true}' in last["content"]


@docker_only
def test_docs_broken_with_repro_and_quote_passes_gate():
    result = make_worker(broken_run()).run()
    assert result.verdict.status == "docs_broken"
    assert result.gate.reproduced and result.gate.explained
    assert result.gate.action == "write_fix"
    assert result.gate.fixed is None
    assert "attributeerror" in _signature(result.gate.repro_output_tail)


@docker_only
def test_docs_broken_without_evidence_is_demoted_to_unsure():
    result = make_worker(broken_run(fetch=False)).run()
    assert result.verdict.status == "unsure"
    assert result.verdict.model_status == "docs_broken"
    assert result.gate.action == "flag"
    assert result.gate.reproduced is True
    assert result.gate.explained is False


@docker_only
def test_made_up_quote_is_not_explained():
    result = make_worker(broken_run(quote="dumps2 was renamed to dump_string")).run()
    assert result.verdict.status == "unsure"
    assert result.gate.explained is False


@docker_only
def test_failure_that_does_not_reproduce_is_unsure():
    # The agent's own diagnosis command broke the sandbox; the page itself is fine.
    # The replay skips diagnose actions, so in a fresh sandbox the step passes.
    step = "if [ -e /tmp/broken-by-agent ]; then echo 'OSError: busy'; exit 1; fi; echo ok"
    turns = [
        [("run", {"command": "touch /tmp/broken-by-agent", "step": 1, "purpose": "diagnose"})],
        [("run", {"command": step, "step": 1, "purpose": "follow"})],
        [("fetch", {"url": CHANGELOG_URL})],
        [report("docs_broken", failing_step={"index": 1, "text": "step"}, evidence={"command": step, "output_tail": "OSError"},
                cause="c", cause_source=CHANGELOG_URL, cause_quote="`json.dumps2` was removed", suggested_fix="f")],
    ]
    result = make_worker(turns).run()
    assert result.gate.reproduced is False
    assert result.verdict.status == "unsure"


@docker_only
def test_gate_off_sends_everything():
    result = make_worker(broken_run(fetch=False), gate=False).run()
    assert result.verdict.status == "docs_broken"
    assert result.gate.action == "send_fix"


@docker_only
def test_budget_gives_up_instead_of_faking_a_pass():
    turns = [[("run", {"command": "true", "step": 1, "purpose": "follow"})]] * 10
    result = make_worker(turns, max_turns=3).run()
    assert result.verdict.status == "agent_gave_up"
    assert result.turns == 3


@docker_only
def test_invalid_report_is_returned_to_model_and_retried():
    turns = [[("report", {"status": "great"})], [report("pass")]]
    w = make_worker(turns)
    result = w.run()
    assert result.verdict.status == "pass"
    err = w.client.calls[1]["messages"][-1]
    assert err["role"] == "tool" and err["content"].startswith("ERROR: Invalid report")


@docker_only
def test_model_that_stops_without_report_gives_up():
    result = make_worker([]).run()
    assert result.verdict.status == "agent_gave_up"
    assert result.turns == 2  # one nudge, then give up


@docker_only
def test_repeated_failures_trigger_decide_note():
    turns = [[("run", {"command": "false", "step": 1, "purpose": "retry"})]] * 3 + [[report("unsure")]]
    w = make_worker(turns)
    w.run()
    third_result = w.client.calls[3]["messages"][-1]["content"]
    assert "has failed 3 times" in third_result


def test_request_shape_for_openrouter(monkeypatch):
    monkeypatch.setenv("NEWT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    calls = []

    class Stop(Exception):
        pass

    def send(**kw):
        calls.append(kw)
        raise Stop

    client = SimpleNamespace(chat=SimpleNamespace(send=send))
    w = Worker(PAGE, client=client, sandbox_factory=lambda: SimpleNamespace(start=lambda: None, teardown=lambda: None))
    with pytest.raises(Stop):
        w.run()
    kw = calls[0]
    assert kw["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert kw["messages"][0]["role"] == "system" and kw["messages"][1]["role"] == "user"
    assert all(t["type"] == "function" for t in kw["tools"])
    assert {t["function"]["name"] for t in kw["tools"]} == {"run", "write_file", "read_file", "wait_for_port", "fetch", "report"}


@docker_only
def test_bad_json_arguments_go_back_to_the_model():
    turns = [[("run", "{not json")], [report("pass")]]
    w = make_worker(turns)
    result = w.run()
    assert result.verdict.status == "pass"
    err = w.client.calls[1]["messages"][-1]
    assert err["content"].startswith("ERROR: Arguments were not valid JSON")


class RateLimited(Exception):
    def __init__(self, status_code, message, headers=None):
        super().__init__(message)
        self.status_code, self.body, self.headers = status_code, "", headers or {}


def test_chat_backs_off_on_429_then_succeeds():
    fake = FakeClient([[report("pass")]])
    attempts = []

    def flaky(**kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise RateLimited(429, "Rate limit exceeded: free-models-per-min", {"retry-after": "7"})
        return fake._send(**kw)

    waits = []
    client = SimpleNamespace(chat=SimpleNamespace(send=flaky))
    reply = llm.chat(client, model="m", messages=[{"role": "user", "content": "hi"}], sleep=waits.append)
    assert waits == [7.0, 7.0]
    assert reply.tool_calls[0].name == "report"


def test_chat_stops_on_daily_limit():
    def limited(**kw):
        raise RateLimited(429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day")

    client = SimpleNamespace(chat=SimpleNamespace(send=limited))
    with pytest.raises(llm.DailyLimitReached):
        llm.chat(client, model="m", messages=[], sleep=lambda s: None)


def test_chat_does_not_retry_client_errors():
    calls = []

    def bad(**kw):
        calls.append(1)
        raise RateLimited(400, "bad request")

    with pytest.raises(RateLimited):
        llm.chat(SimpleNamespace(chat=SimpleNamespace(send=bad)), model="m", messages=[], sleep=lambda s: None)
    assert len(calls) == 1


def test_explained_requires_fetched_source_and_real_quote():
    v = Verdict("docs_broken", cause_source="https://x.test/c/", cause_quote="charge()  was renamed")
    assert _explained(v, {"https://x.test/c": "In 2.0, charge() was renamed to payments.create()."})[0]
    assert not _explained(v, {})[0]
    assert not _explained(v, {"https://x.test/c": "nothing relevant"})[0]


def test_signature_picks_last_error_line():
    out = "Traceback (most recent call last):\n  File \"x\"\nAttributeError: module 'lumen' has no attribute 'charge'\n"
    assert _signature(out) == "attributeerror: module 'lumen' has no attribute 'charge'"
