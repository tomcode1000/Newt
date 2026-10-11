# Newt — neatHack build log

## Project

**Newt — every doc, tried by a newcomer.** An agent that follows a product's
docs in a fresh sandbox exactly as written, works out whether a failure is the
docs' fault, fixes the page, and proves the fix with a clean re-run.

- Event: neatHack (neatlogs), track "Build an agent that works (literally)"
- Team: **topspot** (solo)
- Build window: 10 Oct 2026 07:00 UTC → 12 Oct 2026

## Stack

- Python 3.12, plain tool-calling loop (no agent framework)
- Models: OpenRouter via the official `openrouter` SDK. Model from `NEWT_MODEL`,
  default `nvidia/nemotron-3-super-120b-a12b:free` (from phase 2; phase 1 was
  written against the Anthropic SDK and never made a live call)
- Sandboxes: Docker, fresh `python:3.12-slim` container per page run
- Tracing: neatlogs (`neatlogs.wrap(OpenRouter(...))`)
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

Not done yet: no live model run (no `.env` with `ANTHROPIC_API_KEY` yet).

Entire: the Entire CLI was not on PATH during phase 1, so its 7 commits
(9478d3a .. 46ac41e) have no Entire checkpoints. Fixed from phase 2 on:
`entire status` shows the repo enabled for Claude Code (Entire CLI 0.11.4).

### 11 Oct 2026 — phase 2

- Model calls moved from Anthropic to OpenRouter (`openrouter` SDK 1.3.43,
  chat completions with function tools), traced with
  `neatlogs.wrap(OpenRouter(...))`. New `newt/llm.py`: one `chat()` used by
  everything; backs off on 429 / 5xx (Retry-After or rate-limit reset header,
  else exponential, max 90 s, 8 attempts) and stops with a clear error when the
  free-model daily limit is hit instead of faking a run. Claude-only bits
  removed (adaptive thinking, effort, prompt caching, refusal fallback). Worker
  loop, Verdict and gate unchanged; 30 tests pass (3 new: 429 backoff, daily
  limit, no retry on 400; 1 new: bad JSON tool arguments go back to the model).
- Planned default `minimax/minimax-m3:free` does not exist: OpenRouter answered
  404 "This model is unavailable for free. The paid version is available now".
  Probed free tool-capable models with one tool call each:
  `nvidia/nemotron-3-super-120b-a12b:free` (correct call, 3.9 s),
  `nvidia/nemotron-3-ultra-550b-a55b:free` (correct, 47.6 s),
  `poolside/laguna-s-2.1:free` (correct, 23.6 s), `thinkingmachines/inkling:free`
  (403, agentic harnesses only), `google/gemma-4-31b-it:free` (429 upstream).
  Default set to Nemotron 3 Super (free).

## Demo

Not recorded yet.

## Known limits

- Newt cannot complete steps that need sign-ups, phone checks, captchas or
  paid plans; these are reported as "needs a human".

## Submission status

Not submitted.
