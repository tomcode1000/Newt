"""Tools the worker model can call, and the handlers that run them.

Every action is logged with the docs step it served and why it was taken
(follow the page / retry after fixing my own setup / diagnose). The docs-fault
gate replays the follow/retry path in a fresh sandbox, so this log is what
makes a "the docs are broken" claim checkable.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx

from newt import tracing
from newt.sandbox import Sandbox, SandboxError

OUTPUT_TAIL_CHARS = 8_000
FETCH_MAX_CHARS = 30_000
MAX_FAILURES_PER_STEP = 3

PURPOSES = ["follow", "retry", "diagnose"]
STATUSES = ["pass", "docs_broken", "needs_human", "glitch", "unsure", "agent_gave_up"]
STEP_OUTCOMES = ["passed", "failed", "needs_human", "skipped", "not_reached"]

_step = {"type": "integer", "description": "Number of the page step this serves (1-based, from your plan). 0 = setup outside the page."}
_purpose = {
    "type": "string",
    "enum": PURPOSES,
    "description": "follow = doing the step exactly as written; retry = redoing it after fixing YOUR OWN setup/mistake; diagnose = investigating (not part of the step).",
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "run",
        "description": (
            "Run a shell command (bash) in the sandbox, like typing it into a newcomer's terminal. "
            "The working directory and exported variables carry over between calls. "
            "Use background=true for anything that keeps running (servers, watchers); then use wait_for_port."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "step": _step,
                "purpose": _purpose,
                "timeout": {"type": "integer", "description": "Seconds before the command is killed. Default 120, max 900."},
                "background": {"type": "boolean", "description": "Start and return immediately. Default false."},
            },
            "required": ["command", "step", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file in the sandbox (relative paths resolve against the current directory).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "step": _step,
                "purpose": _purpose,
            },
            "required": ["path", "content", "step", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for_port",
        "description": "Wait until something accepts TCP connections on a port inside the sandbox (e.g. after starting a server in the background).",
        "input_schema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer"},
                "timeout": {"type": "integer", "description": "Seconds. Default 30, max 300."},
                "step": _step,
                "purpose": _purpose,
            },
            "required": ["port", "step", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fetch",
        "description": (
            "Read-only HTTP GET from outside the sandbox: changelogs, release notes, PyPI JSON "
            "(https://pypi.org/pypi/<name>/json), library source on GitHub. HTML is converted to markdown. "
            "Use it to find WHY a step fails before blaming the docs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "report",
        "description": "Finish the run with your verdict. Call exactly once, at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": STATUSES},
                "summary": {"type": "string", "description": "One or two sentences on what happened."},
                "steps": {
                    "type": "array",
                    "description": "Your plan with outcomes, in order. Mark steps the page left out as implicit.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "implicit": {"type": "boolean"},
                            "outcome": {"type": "string", "enum": STEP_OUTCOMES},
                        },
                        "required": ["index", "text", "implicit", "outcome"],
                        "additionalProperties": False,
                    },
                },
                "failing_step": {
                    "type": ["object", "null"],
                    "properties": {
                        "index": {"type": "integer"},
                        "text": {"type": "string", "description": "The exact text of the step from the page."},
                    },
                    "required": ["index", "text"],
                    "additionalProperties": False,
                },
                "evidence": {
                    "type": ["object", "null"],
                    "properties": {
                        "command": {"type": "string", "description": "The command that failed, exactly as run."},
                        "output_tail": {"type": "string"},
                    },
                    "required": ["command", "output_tail"],
                    "additionalProperties": False,
                },
                "cause": {"type": "string", "description": "One sentence: why it fails. Empty if status is pass."},
                "cause_source": {"type": "string", "description": "URL you fetched that shows the cause (changelog line, commit, release notes). Empty if none."},
                "cause_quote": {"type": "string", "description": "Exact excerpt copied from cause_source that shows the cause. Empty if none."},
                "suggested_fix": {"type": "string", "description": "The corrected step text. Empty unless status is docs_broken."},
                "packages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
                        "required": ["name", "version"],
                        "additionalProperties": False,
                    },
                },
                "confidence": {"type": "number", "description": "0-1"},
            },
            "required": ["status", "summary", "steps", "failing_step", "evidence", "cause", "cause_source", "cause_quote", "suggested_fix", "packages", "confidence"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class Action:
    """One thing the worker did in the sandbox."""

    kind: str  # run | background | write_file | wait_for_port | read_file | fetch
    input: dict[str, Any]
    step: int | None = None
    purpose: str | None = None
    ok: bool = True
    exit_code: int | None = None
    timed_out: bool = False
    output_tail: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def replayable(self) -> bool:
        return self.kind in ("run", "background", "write_file", "wait_for_port") and self.purpose in ("follow", "retry")


def http_fetch(url: str) -> str:
    """GET a URL; HTML becomes markdown. Raises on HTTP errors."""
    from markdownify import markdownify

    with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": "newt-docs/0.1"}) as client:
        response = client.get(url)
        response.raise_for_status()
    text = response.text
    if "html" in response.headers.get("content-type", ""):
        text = markdownify(text, heading_style="ATX")
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text


@dataclass
class ToolBox:
    sandbox: Sandbox
    fetcher: Callable[[str], str] = http_fetch
    actions: list[Action] = field(default_factory=list)
    fetched: dict[str, str] = field(default_factory=dict)  # url -> full text, for the gate
    _failures: dict[int, int] = field(default_factory=dict)

    def handle(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """Run one tool call. Returns (text for the model, is_error)."""
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None or name == "report":
            return f"Unknown tool: {name}", True
        try:
            return handler(**tool_input), False
        except TypeError as e:
            return f"Bad arguments for {name}: {e}", True
        except (SandboxError, FileNotFoundError, IsADirectoryError, httpx.HTTPError) as e:
            return f"{type(e).__name__}: {e}", True

    # -- handlers ----------------------------------------------------------

    @tracing.span(kind="TOOL", name="sandbox.run")
    def _tool_run(self, command: str, step: int, purpose: str, timeout: int = 120, background: bool = False) -> str:
        if background:
            return self._background(command, step, purpose)
        timeout = max(1, min(int(timeout), 900))
        result = self.sandbox.exec(command, timeout=timeout)
        action = Action(
            "run", {"command": command, "timeout": timeout}, step, purpose,
            ok=result.ok, exit_code=result.exit_code, timed_out=result.timed_out,
            output_tail=result.output[-OUTPUT_TAIL_CHARS:], duration_s=round(result.duration_s, 2),
        )
        self.actions.append(action)
        status = "TIMED OUT" if result.timed_out else f"exit code {result.exit_code}"
        text = f"[{status}, {result.duration_s:.1f}s]\n{_tail(result.output)}"
        return text + self._failure_note(action)

    def _background(self, command: str, step: int, purpose: str) -> str:
        started = time.monotonic()
        proc = self.sandbox.start_background(command)
        time.sleep(1.5)  # let an instant crash show up
        alive = self.sandbox.background_alive(proc.name)
        logs = self.sandbox.background_logs(proc.name, tail_chars=OUTPUT_TAIL_CHARS)
        action = Action(
            "background", {"command": command}, step, purpose, ok=alive,
            exit_code=None if alive else 1, output_tail=logs, duration_s=round(time.monotonic() - started, 2),
        )
        self.actions.append(action)
        state = "running" if alive else "EXITED"
        text = f"[background process {proc.name} (pid {proc.pid}) {state}; log {proc.log_path}]\n{_tail(logs) or '(no output yet)'}"
        return text + self._failure_note(action)

    @tracing.span(kind="TOOL", name="sandbox.write_file")
    def _tool_write_file(self, path: str, content: str, step: int, purpose: str) -> str:
        abspath = self.sandbox.write_file(path, content)
        self.actions.append(Action("write_file", {"path": path, "content": content}, step, purpose))
        return f"Wrote {len(content)} chars to {abspath}"

    @tracing.span(kind="TOOL", name="sandbox.read_file")
    def _tool_read_file(self, path: str) -> str:
        content = self.sandbox.read_file(path)
        self.actions.append(Action("read_file", {"path": path}, output_tail=content[-OUTPUT_TAIL_CHARS:]))
        return _tail(content, 20_000)

    @tracing.span(kind="TOOL", name="sandbox.wait_for_port")
    def _tool_wait_for_port(self, port: int, step: int, purpose: str, timeout: int = 30) -> str:
        timeout = max(1, min(int(timeout), 300))
        started = time.monotonic()
        up = self.sandbox.wait_for_port(int(port), timeout=timeout)
        action = Action(
            "wait_for_port", {"port": int(port), "timeout": timeout}, step, purpose,
            ok=up, exit_code=0 if up else 1, duration_s=round(time.monotonic() - started, 2),
        )
        self.actions.append(action)
        text = f"Port {port} is accepting connections." if up else f"Nothing is listening on port {port} after {timeout}s."
        if not up:
            for name in self.sandbox.background:
                alive = "running" if self.sandbox.background_alive(name) else "EXITED"
                text += f"\n[background {name}: {alive}]\n{_tail(self.sandbox.background_logs(name), 3_000)}"
        return text + self._failure_note(action)

    @tracing.span(kind="TOOL", name="fetch")
    def _tool_fetch(self, url: str) -> str:
        started = time.monotonic()
        try:
            text = self.fetcher(url)
        except httpx.HTTPError as e:
            self.actions.append(Action("fetch", {"url": url}, ok=False, output_tail=str(e), duration_s=round(time.monotonic() - started, 2)))
            raise
        self.fetched[url] = text
        self.actions.append(Action("fetch", {"url": url}, output_tail=text[:500], duration_s=round(time.monotonic() - started, 2)))
        if len(text) > FETCH_MAX_CHARS:
            return text[:FETCH_MAX_CHARS] + f"\n[... {len(text) - FETCH_MAX_CHARS} more chars not shown ...]"
        return text

    # -- helpers -----------------------------------------------------------

    def _failure_note(self, action: Action) -> str:
        if action.ok or action.purpose == "diagnose" or action.step is None:
            return ""
        count = self._failures.get(action.step, 0) + 1
        self._failures[action.step] = count
        if count >= MAX_FAILURES_PER_STEP:
            return (
                f"\n\n[newt] Step {action.step} has failed {count} times. Stop retrying it: "
                "decide whose fault this is (agent / docs / human / glitch / unsure) and report or move on."
            )
        return ""


def _tail(text: str, limit: int = OUTPUT_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier chars cut ...]\n" + text[-limit:]


def parse_tool_input(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)
