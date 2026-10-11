"""Terminal live view of a run: one row per page, plus root causes and fixes."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

STATUS_STYLE = {
    "pass": "green", "docs_broken": "red", "needs_human": "magenta", "glitch": "yellow",
    "unsure": "yellow", "agent_gave_up": "yellow", "error": "red", "not_run": "dim",
}
FIX_STYLE = {"verified": "green", "failed": "red", "error": "red", "writing": "cyan", "verifying": "cyan"}


@dataclass
class PageRow:
    name: str
    state: str = "queued"
    status: str = ""
    gate: str = ""
    turns: int = 0
    seconds: float = 0.0
    last: str = ""
    started: float = 0.0


@dataclass
class _State:
    title: str = ""
    rows: dict[str, PageRow] = field(default_factory=dict)
    causes: list[tuple[int, str, list[str]]] = field(default_factory=list)
    fixes: dict[str, tuple[int, str, str]] = field(default_factory=dict)  # page -> (cause, state, note)
    footer: str = ""


class NullView:
    """Same interface, draws nothing (tests, --quiet)."""

    def start(self, title: str, pages: list[str]) -> None: ...
    def page(self, name: str, **fields) -> None: ...
    def causes(self, causes: list[tuple[int, str, list[str]]]) -> None: ...
    def fix(self, page: str, cause_id: int, state: str, note: str = "") -> None: ...
    def footer(self, text: str) -> None: ...
    def stop(self) -> None: ...


class RunView(NullView):
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(stderr=True)
        self._s = _State()
        self._lock = threading.Lock()
        self._live: Live | None = None

    def start(self, title: str, pages: list[str]) -> None:
        self._s = _State(title=title, rows={p: PageRow(p) for p in pages})
        self._live = Live(get_renderable=self._render, console=self.console, refresh_per_second=4, transient=False)
        self._live.start()

    def page(self, name: str, **fields) -> None:
        with self._lock:
            row = self._s.rows.setdefault(name, PageRow(name))
            if fields.get("state") == "running" and not row.started:
                row.started = time.monotonic()
            for k, v in fields.items():
                setattr(row, k, v)

    def causes(self, causes: list[tuple[int, str, list[str]]]) -> None:
        with self._lock:
            self._s.causes = list(causes)

    def fix(self, page: str, cause_id: int, state: str, note: str = "") -> None:
        with self._lock:
            self._s.fixes[page] = (cause_id, state, note)

    def footer(self, text: str) -> None:
        with self._lock:
            self._s.footer = text

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _render(self) -> Group:
        with self._lock:
            s = self._s
            table = Table(title=s.title, expand=True, title_justify="left")
            table.add_column("page", no_wrap=True)
            table.add_column("state")
            table.add_column("verdict")
            table.add_column("gate")
            table.add_column("turns", justify="right")
            table.add_column("time", justify="right")
            table.add_column("last action", overflow="ellipsis", no_wrap=True, ratio=1)
            for row in s.rows.values():
                secs = row.seconds or ((time.monotonic() - row.started) if row.started and row.state == "running" else 0)
                state = Text(row.state, style="cyan" if row.state == "running" else "dim" if row.state == "queued" else "")
                status = Text(row.status, style=STATUS_STYLE.get(row.status, ""))
                table.add_row(row.name, state, status, row.gate, str(row.turns or ""), f"{secs:.0f}s" if secs else "", row.last)
            parts: list = [table]
            if s.causes:
                causes = Table(title="root causes", expand=True, title_justify="left")
                causes.add_column("#", justify="right")
                causes.add_column("cause", ratio=2)
                causes.add_column("pages", ratio=1)
                for cid, summary, pages in s.causes:
                    causes.add_row(str(cid), summary, ", ".join(pages))
                parts.append(causes)
            if s.fixes:
                fixes = Table(title="fixes (re-run from zero in a fresh sandbox)", expand=True, title_justify="left")
                fixes.add_column("page")
                fixes.add_column("cause", justify="right")
                fixes.add_column("state")
                fixes.add_column("note", ratio=1, overflow="ellipsis", no_wrap=True)
                for page, (cid, state, note) in sorted(s.fixes.items()):
                    fixes.add_row(page, str(cid), Text(state, style=FIX_STYLE.get(state, "")), note)
                parts.append(fixes)
            if s.footer:
                parts.append(Text(s.footer))
            return Group(*parts)
