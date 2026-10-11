"""The coordinator: run every page of a docs set in parallel, then group failures by root cause.

    pages -> N workers (fresh sandbox each) -> verdicts -> root-cause groups

A page is a fix candidate when its gate action is write_fix (gate on: a + b
passed) or send_fix (gate off: the model's word). Candidates are grouped by
one model call ("7 pages, same rename in v3.0" -> 1 cause); if that answer is
unusable, by the shape of the failure (error signature) instead.

Every state change goes to the ledger first. If the model's daily quota runs
out, pages not yet started are marked not_run and the run stops honestly.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from newt import llm, tracing
from newt.config import DocsConfig, load_config
from newt.ledger import Ledger
from newt.ui import NullView
from newt.worker import Worker, WorkerResult, _signature

FIX_ACTIONS = ("write_fix", "send_fix")


@dataclass
class Cause:
    id: int
    summary: str
    pages: list[str]
    source: str = ""
    quote: str = ""
    method: str = "model"


@dataclass
class RunReport:
    run_id: int
    gate: bool
    results: dict[str, WorkerResult] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    not_run: list[str] = field(default_factory=list)
    causes: list[Cause] = field(default_factory=list)
    fixes: list[Any] = field(default_factory=list)  # FixOutcome, filled by the fix stage
    stopped: str = ""  # why the run stopped early, if it did


GROUP_TOOL = {
    "name": "group_causes",
    "description": "Group the failing pages by root cause. Every page must appear in exactly one group.",
    "input_schema": {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "One sentence: the shared root cause."},
                        "pages": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["summary", "pages"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["groups"],
        "additionalProperties": False,
    },
}

GROUP_PROMPT = """You group documentation failures by root cause.
Pages share a cause when one change explains them all (for example the same renamed function or
removed setting in one library release). Different libraries, or unrelated errors, are different
causes. Call group_causes once."""


def find_pages(docs: Path) -> list[Path]:
    if docs.is_file():
        return [docs]
    return sorted(p for p in docs.glob("*.md") if p.name.lower() != "readme.md")


class Coordinator:
    def __init__(
        self,
        docs: Path,
        *,
        ledger: Ledger,
        workers: int = 4,
        gate: bool = True,
        fix: bool = True,
        view: NullView | None = None,
        client: Any = None,
        model: str | None = None,
        config: DocsConfig | None = None,
        max_turns: int = 60,
        worker_factory: Callable[..., Worker] = Worker,
    ) -> None:
        self.docs = docs
        self.ledger = ledger
        self.workers = workers
        self.gate = gate
        self.fix = fix
        self.view = view or NullView()
        self.client = client
        self.model = model or tracing.model_name()
        self.config = config or load_config(docs)
        self.max_turns = max_turns
        self.worker_factory = worker_factory
        self._stop = threading.Event()
        self._stop_reason = ""

    @tracing.span(kind="CHAIN", name="newt.coordinator")
    def run(self) -> RunReport:
        pages = find_pages(self.docs)
        if not pages:
            raise FileNotFoundError(f"no .md pages in {self.docs}")
        if self.client is None:
            self.client = tracing.model_client()
        run_id = self.ledger.start_run(str(self.docs), self.model, self.gate)
        report = RunReport(run_id=run_id, gate=self.gate)
        for p in pages:
            self.ledger.add_page(run_id, p.name, str(p))
        self.view.start(f"newt run {self.docs} - {self.model} - gate {'ON' if self.gate else 'OFF (fix-everything baseline)'}", [p.name for p in pages])
        try:
            with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="newt-worker") as pool:
                list(pool.map(lambda p: self._run_page(run_id, p, report), pages))

            report.causes = self.group(run_id, report)
            self.view.causes([(c.id, c.summary, c.pages) for c in report.causes])
            if self.fix and report.causes and not self._stop.is_set():
                from newt.fixer import fix_and_verify

                report.fixes = fix_and_verify(self, run_id, report)
        except llm.DailyLimitReached as e:
            self._halt(str(e))
        finally:
            report.stopped = self._stop_reason
            self.ledger.finish_run(run_id, "stopped" if report.stopped else "done", report.stopped)
            self.view.footer(f"stopped: {report.stopped}" if report.stopped else "")
            self.view.stop()
        return report

    def _halt(self, reason: str) -> None:
        if not self._stop.is_set():
            self._stop_reason = reason
            self._stop.set()

    def new_worker(self, page_text: str, page_name: str, *, gate: bool, on_event: Callable[[str], None]) -> Worker:
        return self.worker_factory(
            page_text, page_name, client=self.client, model=self.model, gate=gate, max_turns=self.max_turns,
            sandbox_factory=self.config.sandbox_factory(), fetcher=self.config.fetcher(), on_event=on_event,
        )

    def _run_page(self, run_id: int, path: Path, report: RunReport) -> None:
        name = path.name
        if self._stop.is_set():
            self.ledger.page_failed(run_id, name, self._stop_reason, state="not_run")
            self.view.page(name, state="not_run", status="not_run")
            report.not_run.append(name)
            return
        self.ledger.page_started(run_id, name)
        self.view.page(name, state="running")

        def on_event(msg: str) -> None:
            self.ledger.event(run_id, msg, page=name)
            self.view.page(name, last=msg)

        worker = self.new_worker(path.read_text(encoding="utf-8"), name, gate=self.gate, on_event=on_event)
        try:
            result = worker.run()
        except llm.DailyLimitReached as e:
            self._halt(str(e))
            self.ledger.page_failed(run_id, name, str(e), state="not_run")
            self.view.page(name, state="not_run", status="not_run", last="daily model limit reached")
            report.not_run.append(name)
            return
        except Exception as e:  # one page's crash must not take the run down
            msg = f"{type(e).__name__}: {e}"
            self.ledger.page_failed(run_id, name, msg)
            self.view.page(name, state="error", status="error", last=msg[:200])
            report.errors[name] = msg
            return
        self.ledger.page_done(run_id, name, result)
        report.results[name] = result
        self.view.page(name, state="done", status=result.verdict.status, gate=result.gate.action,
                       turns=result.turns, seconds=result.duration_s, last=result.verdict.summary[:200])

    # -- root causes ----------------------------------------------------------

    @tracing.span(kind="CHAIN", name="group_root_causes")
    def group(self, run_id: int, report: RunReport) -> list[Cause]:
        candidates = {n: r for n, r in sorted(report.results.items()) if r.gate.action in FIX_ACTIONS}
        if not candidates:
            return []
        groups: list[tuple[str, list[str]]] | None = None
        method = "model"
        if len(candidates) == 1:
            name, r = next(iter(candidates.items()))
            groups, method = [(r.verdict.cause or r.verdict.summary, [name])], "single"
        elif not self._stop.is_set():
            groups = self._group_with_model(candidates)
        if groups is None:
            groups, method = _group_by_signature(candidates), "signature"

        causes = []
        for i, (summary, names) in enumerate(groups, start=1):
            first = candidates[names[0]].verdict
            cause = Cause(i, summary, names, first.cause_source, first.cause_quote, method)
            self.ledger.add_cause(run_id, i, summary, names, cause.source, cause.quote, method)
            self.ledger.event(run_id, f"cause {i} ({method}): {summary} -> {', '.join(names)}")
            causes.append(cause)
        return causes

    def _group_with_model(self, candidates: dict[str, WorkerResult]) -> list[tuple[str, list[str]]] | None:
        items = []
        for name, r in candidates.items():
            v = r.verdict
            items.append({
                "page": name, "cause": v.cause, "cause_source": v.cause_source, "cause_quote": v.cause_quote,
                "failing_step": v.failing_step, "error": _signature((v.evidence or {}).get("output_tail", "") or r.gate.repro_output_tail),
                "packages": v.packages,
            })
        messages = [
            {"role": "system", "content": GROUP_PROMPT},
            {"role": "user", "content": "Failing pages:\n" + json.dumps(items, indent=2)},
        ]
        try:
            reply = llm.chat(self.client, model=self.model, messages=messages, tools=llm.function_tools([GROUP_TOOL]), max_tokens=4000)
        except llm.DailyLimitReached:
            raise
        except Exception:
            return None
        for call in reply.tool_calls:
            if call.name != "group_causes":
                continue
            try:
                groups = [(g["summary"], list(g["pages"])) for g in json.loads(call.arguments)["groups"]]
            except (ValueError, KeyError, TypeError):
                return None
            listed = [p for _, pages in groups for p in pages]
            if sorted(listed) == sorted(candidates) and all(pages for _, pages in groups):
                return groups
        return None


def _group_by_signature(candidates: dict[str, WorkerResult]) -> list[tuple[str, list[str]]]:
    by_sig: dict[str, list[str]] = {}
    for name, r in candidates.items():
        sig = _signature((r.verdict.evidence or {}).get("output_tail", "") or r.gate.repro_output_tail) or r.verdict.cause
        by_sig.setdefault(sig, []).append(name)
    return [(candidates[names[0]].verdict.cause or sig, names) for sig, names in by_sig.items()]


def run_summary(ledger: Ledger, run_id: int) -> dict[str, Any]:
    """Everything the ledger knows about one run, without the bulky per-action logs."""
    pages = []
    for row in ledger.pages(run_id):
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        gate = result.get("gate") or {}
        verdict = result.get("verdict") or {}
        pages.append({
            "page": row["name"], "state": row["state"], "status": row["status"], "model_status": row["model_status"],
            "gate_action": row["gate_action"], "gate_reasons": gate.get("reasons", []), "summary": row["summary"],
            "cause": verdict.get("cause", ""), "cause_source": verdict.get("cause_source", ""),
            "turns": row["turns"], "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
            "duration_s": row["duration_s"], "error": row["error"],
        })
    fixes = [{k: v for k, v in f.items() if k not in ("verify_json", "run_id")} for f in ledger.fixes(run_id)]
    return {"run": ledger.run(run_id), "pages": pages, "causes": ledger.causes(run_id), "fixes": fixes}
