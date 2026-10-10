# Newt — neatHack build log

## Project

**Newt — every doc, tried by a newcomer.** An agent that follows a product's
docs in a fresh sandbox exactly as written, works out whether a failure is the
docs' fault, fixes the page, and proves the fix with a clean re-run.

- Event: neatHack (neatlogs), track "Build an agent that works (literally)"
- Team: **topspot** (solo)
- Build window: 10 Oct 2026 07:00 UTC → 12 Oct 2026

## Stack

- Python 3.12, Anthropic SDK (plain tool-use loop, no agent framework)
- Workers: `claude-sonnet-5-5`; coordinator: `claude-opus-5-5`
- Sandboxes: Docker, fresh `python:3.12-slim` container per page run
- Tracing: neatlogs (`neatlogs.wrap(Anthropic())`)
- Repo history: Entire checkpoints
- State: SQLite ledger (planned)

## Build log

### 10 Oct 2026 — phase 1 (foundation)

- Repo initialised: README placeholder, MIT LICENSE, this log.
- Package skeleton (`pyproject.toml`, `newt` command, editable install).
- `newt/tracing.py`: keys from `.env` (environment wins), neatlogs `init`/`wrap`/`flush`;
  runs untraced if `NEATLOGS_API_KEY` is missing. `.env.example` added.
- `newt/sandbox.py`: fresh `python:3.12-slim` container per run; exec with a
  timeout (in-container `timeout` + host-side backstop); background processes;
  `wait_for_port`; file read/write; teardown. The working directory and exported
  variables persist between commands, so `cd` and `source .venv/bin/activate`
  behave as they would in one terminal. 9 tests against real Docker pass.
- `newt/tools.py` + `newt/worker.py`: tool-use loop on `claude-sonnet-5-5`
  (adaptive thinking, effort medium, prompt caching, server-side refusal
  fallback), Verdict JSON (pass / docs_broken / needs_human / glitch / unsure /
  agent_gave_up), turn and token budget, `newt page <file.md>`.
- DOCS-FAULT GATE: a `docs_broken` verdict is checked against evidence: (a) the
  failing step is replayed in a fresh sandbox (follow/retry actions only, not
  diagnosis) and must fail the same way; (b) the cited source must have been
  fetched in the run and contain the quoted text. a+b -> write_fix; otherwise
  status becomes `unsure`. (c) fixed-on-re-run waits for the fixer/verifier.
  `--no-gate` gives the fix-everything baseline.
- Worker tests use a scripted fake model with real Docker sandboxes (13 tests):
  gate passes real breaks, demotes unexplained ones, rejects a made-up quote,
  and rejects a failure the agent caused itself (doesn't reproduce).
- Demo page `demo/pages/notes-quickstart.md` (Flask, background server, needs
  curl which the sandbox lacks). Checked by hand in a sandbox: returns
  `["hello"]` once curl is installed.

Failures / fixes today:
- Three worker tests failed at first: the fake client stored a reference to
  the growing `messages` list, so assertions saw the final state. Fixed by
  snapshotting each call. (Test bug, not a worker bug.)
- A first draft of the "doesn't reproduce" test would have reproduced in a
  fresh sandbox too; rewritten so the agent's own diagnose command causes the
  failure, which the replay correctly skips.

Not done yet: no live model run (no `.env` with `ANTHROPIC_API_KEY` yet);
Entire CLI not on PATH, so commits so far have no Entire checkpoints.

## Demo

Not recorded yet.

## Known limits

- Newt cannot complete steps that need sign-ups, phone checks, captchas or
  paid plans; these are reported as "needs a human".

## Submission status

Not submitted.
