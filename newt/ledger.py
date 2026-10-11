"""SQLite run ledger: every state change is written here before Newt acts on it.

Tables: runs, pages (one row per page per run), causes (root-cause groups),
fixes (one rewritten page each, with its verification), events (timeline).
One connection shared by the worker threads, guarded by a lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    docs TEXT NOT NULL,
    model TEXT NOT NULL,
    gate INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running | done | stopped
    note TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS pages (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | error | not_run
    status TEXT,                           -- the Verdict status
    model_status TEXT,
    gate_action TEXT,
    summary TEXT,
    result_json TEXT,
    turns INTEGER, input_tokens INTEGER, output_tokens INTEGER, duration_s REAL,
    error TEXT,
    started_at REAL, finished_at REAL,
    PRIMARY KEY (run_id, name)
);
CREATE TABLE IF NOT EXISTS causes (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    pages_json TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    quote TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',  -- model | signature
    PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS fixes (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    cause_id INTEGER NOT NULL,
    page TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'writing',  -- writing | verifying | verified | failed | error
    fixed_text TEXT,
    verify_status TEXT,
    verify_json TEXT,
    sent INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY (run_id, page)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    ts REAL NOT NULL,
    page TEXT,
    message TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, params)

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # -- runs ----------------------------------------------------------------

    def start_run(self, docs: str, model: str, gate: bool) -> int:
        cur = self._exec("INSERT INTO runs (docs, model, gate, started_at) VALUES (?, ?, ?, ?)", (docs, model, int(gate), time.time()))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str = "done", note: str = "") -> None:
        self._exec("UPDATE runs SET status = ?, note = ?, finished_at = ? WHERE id = ?", (status, note, time.time(), run_id))

    def run(self, run_id: int) -> dict[str, Any]:
        return self._query("SELECT * FROM runs WHERE id = ?", (run_id,))[0]

    # -- pages ---------------------------------------------------------------

    def add_page(self, run_id: int, name: str, path: str) -> None:
        self._exec("INSERT INTO pages (run_id, name, path) VALUES (?, ?, ?)", (run_id, name, path))

    def page_started(self, run_id: int, name: str) -> None:
        self._exec("UPDATE pages SET state = 'running', started_at = ? WHERE run_id = ? AND name = ?", (time.time(), run_id, name))

    def page_done(self, run_id: int, name: str, result: Any) -> None:
        v, g = result.verdict, result.gate
        self._exec(
            """UPDATE pages SET state = 'done', status = ?, model_status = ?, gate_action = ?, summary = ?,
               result_json = ?, turns = ?, input_tokens = ?, output_tokens = ?, duration_s = ?, finished_at = ?
               WHERE run_id = ? AND name = ?""",
            (v.status, v.model_status, g.action, v.summary, result.to_json(), result.turns, result.input_tokens,
             result.output_tokens, result.duration_s, time.time(), run_id, name),
        )

    def page_failed(self, run_id: int, name: str, error: str, state: str = "error") -> None:
        self._exec("UPDATE pages SET state = ?, error = ?, finished_at = ? WHERE run_id = ? AND name = ?", (state, error, time.time(), run_id, name))

    def update_gate(self, run_id: int, name: str, action: str, result_json: str) -> None:
        self._exec("UPDATE pages SET gate_action = ?, result_json = ? WHERE run_id = ? AND name = ?", (action, result_json, run_id, name))

    def pages(self, run_id: int) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM pages WHERE run_id = ? ORDER BY name", (run_id,))

    # -- causes and fixes ----------------------------------------------------

    def add_cause(self, run_id: int, cause_id: int, summary: str, pages: list[str], source: str = "", quote: str = "", method: str = "") -> None:
        self._exec(
            "INSERT INTO causes (run_id, id, summary, pages_json, source, quote, method) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, cause_id, summary, json.dumps(pages), source, quote, method),
        )

    def causes(self, run_id: int) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM causes WHERE run_id = ? ORDER BY id", (run_id,))
        for r in rows:
            r["pages"] = json.loads(r.pop("pages_json"))
        return rows

    def add_fix(self, run_id: int, cause_id: int, page: str) -> None:
        self._exec("INSERT INTO fixes (run_id, cause_id, page) VALUES (?, ?, ?)", (run_id, cause_id, page))

    def update_fix(self, run_id: int, page: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE fixes SET {cols} WHERE run_id = ? AND page = ?", (*fields.values(), run_id, page))

    def fixes(self, run_id: int) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM fixes WHERE run_id = ? ORDER BY page", (run_id,))

    # -- events --------------------------------------------------------------

    def event(self, run_id: int, message: str, page: str | None = None) -> None:
        self._exec("INSERT INTO events (run_id, ts, page, message) VALUES (?, ?, ?, ?)", (run_id, time.time(), page, message))

    def events(self, run_id: int) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM events WHERE run_id = ? ORDER BY id", (run_id,))

    def close(self) -> None:
        with self._lock:
            self._db.close()
