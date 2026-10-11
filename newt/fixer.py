"""Fixer + verifier: rewrite each page for its root cause, then prove the rewrite from zero.

For every page in a cause:
  1. fixer   - one model call returns the whole corrected page (smallest change
               that removes the cause; everything else stays as written)
  2. verify  - a FRESH worker follows the corrected page in a FRESH sandbox,
               exactly like the first run. Verified only if it reports pass.

This is check (c) of the docs-fault gate. Gate on: only verified fixes are
sent (action send_fix); a fix that fails its re-run stays write_fix, unsent.
Gate off (fix-everything baseline): every fix is sent; the re-run is still
recorded, so the baseline's wrong fixes can be counted.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from newt import llm, tracing

if TYPE_CHECKING:
    from newt.coordinator import Cause, Coordinator, RunReport

FIXES_DIR = Path("runs") / "fixes"

FIX_PROMPT = """You repair a documentation page that no longer works.
You get the page, the evidence of how it fails, and the source that explains why (changelog, release notes).
Return the WHOLE corrected page with the write_page tool.
Rules:
- Make the smallest change that makes the page work with the current release. Fix everything the source says
  changed that this page relies on (renamed functions, settings, environment variables, outputs it promises),
  not only the first error.
- Keep everything else exactly as written: headings, wording, order, unrelated steps, links.
- Do not add workarounds for the reader's machine (installing tools, system packages); only fix what the page
  itself gets wrong.
- Do not invent APIs. Use only what the source or the evidence shows."""

WRITE_PAGE_TOOL = {
    "name": "write_page",
    "description": "Return the corrected page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The whole corrected page, markdown."},
            "changes": {"type": "string", "description": "One or two sentences: what you changed and why."},
        },
        "required": ["content", "changes"],
        "additionalProperties": False,
    },
}


@dataclass
class FixOutcome:
    page: str
    cause_id: int
    state: str  # verified | failed | error
    verify_status: str = ""
    sent: bool = False
    changes: str = ""
    path: str = ""
    error: str = ""


def fix_and_verify(coord: "Coordinator", run_id: int, report: "RunReport") -> list[FixOutcome]:
    jobs = [(cause, page) for cause in report.causes for page in cause.pages]
    for cause, page in jobs:
        coord.ledger.add_fix(run_id, cause.id, page)
        coord.view.fix(page, cause.id, "writing")
    with ThreadPoolExecutor(max_workers=coord.workers, thread_name_prefix="newt-fixer") as pool:
        futures = [pool.submit(_fix_one, coord, run_id, report, cause, page) for cause, page in jobs]
        outcomes = []
        limit_error: llm.DailyLimitReached | None = None
        for f in futures:
            try:
                outcomes.append(f.result())
            except llm.DailyLimitReached as e:
                limit_error = limit_error or e
    if limit_error is not None:
        raise limit_error
    return outcomes


@tracing.span(kind="CHAIN", name="fix_and_verify_page")
def _fix_one(coord: "Coordinator", run_id: int, report: "RunReport", cause: "Cause", page: str) -> FixOutcome:
    result = report.results[page]
    original = (coord.docs / page if coord.docs.is_dir() else coord.docs).read_text(encoding="utf-8")
    try:
        fixed, changes = write_fix(coord.client, coord.model, original, cause, result)
    except llm.DailyLimitReached:
        coord.ledger.update_fix(run_id, page, state="error", error="daily model limit reached")
        coord.view.fix(page, cause.id, "error", "daily model limit reached")
        raise
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        coord.ledger.update_fix(run_id, page, state="error", error=msg)
        coord.view.fix(page, cause.id, "error", msg[:200])
        return FixOutcome(page, cause.id, "error", error=msg)

    out = FIXES_DIR / f"run-{run_id}" / page
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(fixed, encoding="utf-8")
    coord.ledger.update_fix(run_id, page, state="verifying", fixed_text=fixed)
    coord.ledger.event(run_id, f"fix written for cause {cause.id}: {changes}", page=page)
    coord.view.fix(page, cause.id, "verifying", changes)

    # Check (c): the corrected page, followed from zero by a fresh worker in a fresh sandbox.
    def on_event(msg: str) -> None:
        coord.ledger.event(run_id, f"[verify] {msg}", page=page)
        coord.view.fix(page, cause.id, "verifying", msg)

    verifier = coord.new_worker(fixed, page, gate=True, on_event=on_event)
    try:
        check = verifier.run()
    except llm.DailyLimitReached:
        coord.ledger.update_fix(run_id, page, state="error", error="daily model limit reached during verification")
        coord.view.fix(page, cause.id, "error", "daily model limit reached")
        raise
    verified = check.verdict.status == "pass"

    gate = result.gate
    gate.fixed = verified
    gate.reasons = [r for r in gate.reasons if not r.startswith("fixed:")]
    gate.reasons.append(f"fixed: {'re-run from zero passed' if verified else f're-run from zero did not pass ({check.verdict.status})'}")
    if coord.gate:
        gate.action = "send_fix" if verified else "write_fix"
        sent = verified
    else:
        sent = True  # baseline sends every fix, verified or not
    coord.ledger.update_gate(run_id, page, gate.action, result.to_json())

    state = "verified" if verified else "failed"
    coord.ledger.update_fix(run_id, page, state=state, verify_status=check.verdict.status,
                            verify_json=check.to_json(), sent=int(sent))
    note = f"re-run: {check.verdict.status}{' - sent' if sent else ' - not sent'}"
    coord.ledger.event(run_id, f"fix {state} ({note})", page=page)
    coord.view.fix(page, cause.id, state, note)
    return FixOutcome(page, cause.id, state, check.verdict.status, sent, changes, str(out))


@tracing.span(kind="AGENT", name="newt.fixer", role="docs repairer")
def write_fix(client: Any, model: str, original: str, cause: "Cause", result: Any) -> tuple[str, str]:
    """One model call: the whole corrected page, and a note on what changed."""
    v = result.verdict
    brief = {
        "root_cause": cause.summary,
        "failing_step": v.failing_step,
        "evidence": v.evidence,
        "worker_cause": v.cause,
        "suggested_fix": v.suggested_fix,
        "cause_source": v.cause_source,
        "cause_quote": v.cause_quote,
        "installed_packages": v.packages,
    }
    user = f"<page>\n{original}\n</page>\n\n<failure>\n{json.dumps(brief, indent=2)}\n</failure>"
    if result.cause_source_text:
        user += f"\n\n<source url=\"{v.cause_source}\">\n{result.cause_source_text}\n</source>"
    messages = [{"role": "system", "content": FIX_PROMPT}, {"role": "user", "content": user}]
    tools = llm.function_tools([WRITE_PAGE_TOOL])
    for _ in range(2):
        reply = llm.chat(client, model=model, messages=messages, tools=tools, max_tokens=16000)
        messages.append(reply.message)
        problem = "Call write_page with the whole corrected page."
        for call in reply.tool_calls:
            try:
                data = json.loads(call.arguments)
            except ValueError as e:
                problem = f"Arguments were not valid JSON ({e})."
                messages.append({"role": "tool", "tool_call_id": call.id, "content": f"ERROR: {problem}"})
                continue
            content = (data.get("content") or "").strip()
            if call.name == "write_page" and content and content != original.strip():
                return content + "\n", data.get("changes", "")
            problem = "The page is empty or unchanged."
            messages.append({"role": "tool", "tool_call_id": call.id, "content": f"ERROR: {problem}"})
        if not reply.tool_calls:
            messages.append({"role": "user", "content": problem})
    raise ValueError(f"fixer did not return a usable page: {problem}")
