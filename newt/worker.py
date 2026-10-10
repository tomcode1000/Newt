"""The worker: one docs page, one fresh sandbox, one Verdict.

The model follows the page and reports a verdict. A `docs_broken` verdict
then goes through the DOCS-FAULT GATE, which trusts evidence, not the model:

  a. reproduces: the same step fails the same way in a FRESH sandbox
  b. explained:  the cited source was actually fetched in this run and
                 contains the quoted text that shows the cause
  c. fixed:      the corrected page passes a full re-run from zero
                 (needs the fixer/verifier - not built yet, so c is pending)

  a + b -> write the fix;  a + b + c -> send it;  missing a or b -> UNSURE.

With gate=False (the "fix-everything" baseline) every docs_broken verdict
goes straight to "send".
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from newt import tracing
from newt.sandbox import Sandbox
from newt.tools import STATUSES, TOOL_SCHEMAS, Action, ToolBox, http_fetch, parse_tool_input

WORKER_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are Newt, a brand-new user following a product's documentation page for the first time.
Your job is to find out whether the page works for a newcomer, exactly as written.

THE SANDBOX
- A fresh Debian container (python:3.12-slim), you are root, working directory /work, network on.
- It is emptier than a typical developer machine: curl, git, build tools and editors are NOT installed.
  Installing one of those with apt-get is fixing YOUR setup (purpose "retry" or step 0), not a docs fault.
- Each `run` is a new bash, but the working directory and exported variables carry over, like one terminal.
- Anything long-running (servers, "in another terminal") must use run with background=true, then wait_for_port.

HOW TO WORK
1. Plan: list the page's steps in order. Add steps the author left out (e.g. "create a venv",
   "needs Python 3.11+") and mark them implicit.
2. Follow each step EXACTLY as written first (purpose "follow"). Never improve or modernise the docs
   while following them - that would hide the very break you are looking for.
3. Read the output after every action. A step passes only if it did what the page says it should.
4. On a failure, decide whose fault it is:
   AGENT  - you mistyped, misread a step, or your sandbox lacks something any reader's machine would have.
            Fix your side and retry (purpose "retry"), at most 3 times per step.
   DOCS   - the page, as written, cannot work. Find out WHY before saying so: fetch the changelog, release
            notes, PyPI JSON or library source, and note the installed version.
   HUMAN  - the step needs a sign-up, phone or email check, payment, captcha, or a real account.
            Do not attempt it; mark it and continue with later steps if possible.
   GLITCH - a network timeout, registry 5xx or rate limit. Wait (run `sleep 15`) and re-run the step,
            up to 2 times. Not a docs fault and not something to fix in the docs.
   UNSURE - none of the above is clearly true. Do not guess.
5. Report once with the `report` tool.

VERDICT STATUS
- pass: every step did what the page said.
- docs_broken: the page as written fails. You MUST give failing_step (index + exact step text), evidence
  (the exact failing command + output tail), cause, and suggested_fix. cause_source must be a URL you
  fetched in this run and cause_quote an exact excerpt copied from it. Without those, use unsure.
- needs_human: the page cannot be finished without a human-only step, and nothing else failed.
- glitch: failures were transient (network, registry) and persisted after retries.
- unsure: a failure you cannot attribute with evidence.
- agent_gave_up: you could not complete the page for reasons on your side.
Use `diagnose` as the purpose for any command that investigates rather than follows a step.
List installed packages with versions in `packages` (e.g. from `pip list`) when anything was installed."""


@dataclass
class GateDecision:
    reproduced: bool | None = None
    explained: bool | None = None
    fixed: bool | None = None  # needs the verifier; None until it exists
    action: str = "none"  # none | flag | write_fix | send_fix
    reasons: list[str] = field(default_factory=list)
    repro_output_tail: str = ""


@dataclass
class Verdict:
    status: str
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    failing_step: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    cause: str = ""
    cause_source: str = ""
    cause_quote: str = ""
    suggested_fix: str = ""
    packages: list[dict[str, str]] = field(default_factory=list)
    confidence: float = 0.0
    model_status: str | None = None  # what the model said, if the gate changed it

    @classmethod
    def from_report(cls, data: dict[str, Any]) -> "Verdict":
        status = data.get("status")
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
        known = {f for f in cls.__dataclass_fields__ if f != "model_status"}
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})


@dataclass
class WorkerResult:
    page: str
    verdict: Verdict
    gate: GateDecision
    actions: list[Action]
    turns: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    duration_s: float
    model: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["actions"] = [a.to_dict() for a in self.actions]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class Worker:
    def __init__(
        self,
        page_text: str,
        page_name: str = "page",
        *,
        client: Any = None,
        model: str = WORKER_MODEL,
        effort: str = "medium",
        gate: bool = True,
        max_turns: int = 60,
        max_input_tokens: int = 2_000_000,
        sandbox_factory: Callable[[], Sandbox] = Sandbox,
        fetcher: Callable[[str], str] = http_fetch,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        self.page_text = page_text
        self.page_name = page_name
        self.client = client
        self.model = model
        self.effort = effort
        self.gate_on = gate
        self.max_turns = max_turns
        self.max_input_tokens = max_input_tokens
        self.sandbox_factory = sandbox_factory
        self.fetcher = fetcher
        self.on_event = on_event or (lambda msg: None)

    @tracing.span(kind="AGENT", name="newt.worker", role="docs follower")
    def run(self) -> WorkerResult:
        started = time.monotonic()
        client = self.client or tracing.anthropic_client()
        usage = {"in": 0, "out": 0, "cache": 0}
        turns = 0
        verdict: Verdict | None = None

        sandbox = self.sandbox_factory()
        sandbox.start()
        tools = ToolBox(sandbox, fetcher=self.fetcher)
        try:
            messages: list[dict[str, Any]] = [{"role": "user", "content": self._first_message()}]
            nudged = False
            while verdict is None:
                if turns >= self.max_turns or usage["in"] >= self.max_input_tokens:
                    verdict = Verdict("agent_gave_up", summary=f"Budget reached ({turns} turns, {usage['in']} input tokens) before a verdict.")
                    break
                turns += 1
                response = self._call_model(client, messages)
                u = response.usage
                usage["in"] += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
                usage["out"] += u.output_tokens or 0
                usage["cache"] += getattr(u, "cache_read_input_tokens", 0) or 0
                # Append content unchanged: thinking blocks must go back as they came.
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "refusal":
                    verdict = Verdict("agent_gave_up", summary="The model declined to continue (refusal).")
                    break

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    if nudged:
                        verdict = Verdict("agent_gave_up", summary="The model stopped without reporting a verdict.")
                        break
                    nudged = True
                    messages.append({"role": "user", "content": "Continue following the page with the tools, or call `report` if you are done."})
                    continue

                results = []
                for block in tool_uses:
                    tool_input = parse_tool_input(block.input)
                    if block.name == "report":
                        try:
                            verdict = Verdict.from_report(tool_input)
                            text, is_error = "Verdict recorded.", False
                        except (TypeError, ValueError) as e:
                            text, is_error = f"Invalid report: {e}. Fix it and call report again.", True
                    else:
                        self.on_event(f"{block.name}: {_brief(tool_input)}")
                        text, is_error = tools.handle(block.name, tool_input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error})
                if verdict is None:
                    messages.append({"role": "user", "content": results})
        finally:
            sandbox.teardown()

        gate = self._gate(verdict, tools)
        return WorkerResult(
            page=self.page_name, verdict=verdict, gate=gate, actions=tools.actions, turns=turns,
            input_tokens=usage["in"], output_tokens=usage["out"], cache_read_tokens=usage["cache"],
            duration_s=round(time.monotonic() - started, 1), model=self.model,
        )

    def _first_message(self) -> str:
        return (
            f"Follow this documentation page exactly as a newcomer would. Page: {self.page_name}\n\n"
            f"<page>\n{self.page_text}\n</page>"
        )

    def _call_model(self, client: Any, messages: list[dict[str, Any]]) -> Any:
        return client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
            cache_control={"type": "ephemeral"},
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    # -- the docs-fault gate ----------------------------------------------

    @tracing.span(kind="CHAIN", name="docs_fault_gate")
    def _gate(self, verdict: Verdict, tools: ToolBox) -> GateDecision:
        if verdict.status != "docs_broken":
            return GateDecision(action="none", reasons=[f"status is {verdict.status}; gate not needed"])
        if not self.gate_on:
            return GateDecision(action="send_fix", reasons=["gate OFF (fix-everything baseline): model's word accepted"])

        decision = GateDecision()
        decision.reproduced, why, decision.repro_output_tail = self._reproduces(verdict, tools.actions)
        decision.reasons.append(f"reproduces: {why}")
        decision.explained, why = _explained(verdict, tools.fetched)
        decision.reasons.append(f"explained: {why}")
        decision.reasons.append("fixed: pending (verifier not built yet)")

        if decision.reproduced and decision.explained:
            decision.action = "write_fix"
        else:
            decision.action = "flag"
            verdict.model_status = verdict.status
            verdict.status = "unsure"
        self.on_event(f"gate: {decision.action} ({'; '.join(decision.reasons)})")
        return decision

    def _reproduces(self, verdict: Verdict, actions: list[Action]) -> tuple[bool, str, str]:
        """Replay the run's follow/retry path in a fresh sandbox; the failing action must fail again, the same way."""
        if not verdict.failing_step or not isinstance(verdict.failing_step.get("index"), int):
            return False, "no failing_step index in the verdict", ""
        index = verdict.failing_step["index"]
        path = [a for a in actions if a.replayable and a.step is not None]
        failing = [a for a in path if a.step == index and not a.ok]
        if not failing:
            return False, f"no failed follow/retry action recorded for step {index}", ""
        target = failing[-1]
        # Everything that worked before the target, then the target itself.
        target_pos = next(i for i, a in enumerate(path) if a is target)
        before = [a for a in path[:target_pos] if a.ok and a.step <= index]

        with self.sandbox_factory() as fresh:
            for action in before:
                replayed = _replay(fresh, action)
                if not replayed.ok:
                    return False, f"replay diverged before the failing step at {action.kind} {_brief(action.input)}", replayed.output_tail
            again = _replay(fresh, target)

        if again.ok:
            return False, "the failing action succeeded in a fresh sandbox", again.output_tail
        if target.timed_out != again.timed_out:
            return False, "failed differently (timeout vs error)", again.output_tail
        sig_a, sig_b = _signature(target.output_tail), _signature(again.output_tail)
        if sig_a and sig_a != sig_b and sig_a not in _norm(again.output_tail):
            return False, f"failed differently: {sig_a!r} vs {sig_b!r}", again.output_tail
        return True, f"failed again in a fresh sandbox ({sig_b or 'exit ' + str(again.exit_code)})", again.output_tail


def _replay(sandbox: Sandbox, action: Action) -> Action:
    """Re-run one recorded action in another sandbox and record how it went."""
    if action.kind == "run":
        r = sandbox.exec(action.input["command"], timeout=action.input.get("timeout", 120))
        return Action("run", action.input, action.step, action.purpose, ok=r.ok, exit_code=r.exit_code, timed_out=r.timed_out, output_tail=r.output[-8000:])
    if action.kind == "background":
        proc = sandbox.start_background(action.input["command"])
        time.sleep(1.5)
        alive = sandbox.background_alive(proc.name)
        return Action("background", action.input, action.step, action.purpose, ok=alive, output_tail=sandbox.background_logs(proc.name))
    if action.kind == "write_file":
        sandbox.write_file(action.input["path"], action.input["content"])
        return Action("write_file", action.input, action.step, action.purpose)
    if action.kind == "wait_for_port":
        up = sandbox.wait_for_port(action.input["port"], timeout=action.input.get("timeout", 30))
        return Action("wait_for_port", action.input, action.step, action.purpose, ok=up, exit_code=0 if up else 1)
    raise ValueError(f"cannot replay {action.kind}")


def _explained(verdict: Verdict, fetched: dict[str, str]) -> tuple[bool, str]:
    source = verdict.cause_source.strip()
    if not source:
        return False, "no cause_source cited"
    text = fetched.get(source) or next((t for u, t in fetched.items() if u.rstrip("/") == source.rstrip("/")), None)
    if text is None:
        return False, f"cause_source {source} was not fetched in this run"
    quote = _norm(verdict.cause_quote)
    if len(quote) < 8:
        return False, "cause_quote missing or too short to check"
    if quote not in _norm(text):
        return False, "cause_quote does not appear in the fetched source"
    return True, f"quote found in {source}"


_ERROR_LINE = re.compile(r"(\w+(?:Error|Exception|Exit)\b.*|error:.*|ERROR:.*|command not found.*|No such file or directory.*)")


def _signature(output: str) -> str:
    """The last error-looking line, normalised: the 'shape' of a failure."""
    matches = _ERROR_LINE.findall(output or "")
    if matches:
        return _norm(matches[-1])[:200]
    lines = [l for l in (output or "").splitlines() if l.strip()]
    return _norm(lines[-1])[:200] if lines else ""


def _norm(text: str) -> str:
    text = re.sub(r"0x[0-9a-f]+", "0x", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _brief(data: dict[str, Any], limit: int = 120) -> str:
    text = data.get("command") or data.get("path") or data.get("url") or json.dumps(data)
    text = str(text).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."
