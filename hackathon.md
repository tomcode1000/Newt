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
- First live run (`newt page demo/pages/notes-quickstart.md`, Nemotron 3 Super
  free, tracing on): **pass**, 15 model turns, 125,182 input / 3,007 output
  tokens (98,384 cached), 186.4 s. The model installed curl as step 0 (setup,
  not a docs fault), followed steps 1-5, and both curl calls returned
  `["hello"]`. It wrote `app.py` one directory too deep at first, noticed with
  `pwd`, and redid it (purpose "retry") - a recovered agent-side mistake. It
  started the server with `flask ... &` rather than `background=true`; that
  worked here.
- `python -m neatlogs doctor --probe`: PASS (TRACE_VISIBLE, TRACE_FINALIZED,
  hierarchy, attributes, tokens). The doctor reads `NEATLOGS_API_KEY` only from
  the environment, not `.env`, so it reported CREDENTIAL_MISSING until the key
  was exported. Listing Newt's own run trace needs `neatlogs-cli` + a browser
  device login; not done yet.
- Broke and fixed on the way: (1) the planned free MiniMax model id (above);
  (2) Entire's prepare-commit-msg hook hung forever on a non-interactive commit
  - it was waiting at the "Link this commit to session context?" prompt on the
  console. Fixed with `"commit_linking": "always"` in `.entire/settings.json`;
  commits now carry an `Entire-Checkpoint` trailer; (3) two string-escape typos
  of mine (a broken f-string in cli.py, a mangled path in the README), caught by
  the live run before they were committed.
- Demo world (BUILD-PLAN section 4): `demo/lumen-sdk` v1.0 (`lumen.charge()`,
  `LUMEN_API_KEY`) and v2.0 (`lumen.payments.create()`, `LUMEN_SECRET_KEY`,
  changelog). Distribution name `lumenpay` because `lumen-sdk` is a real PyPI
  project; the local index (`demo/lumen_index.py`, port 8765) redirects every
  other project to PyPI so the real names can never shadow ours. `publish 2.0.0`
  releases v2.0 while the index runs. 8 pages in `demo/lumen-docs`: 3 use
  `charge()`, 1 background server (~4 s to listen), 1 SMS phone check, 1 needs
  libxml2, 2 fine. `newt.json` per docs set: sandbox env (pip index) and a
  fetch rewrite - on this laptop the host cannot reach `host.docker.internal`
  (the hosts file has a stale 172.20.10.3) while containers can.
  4 new Docker tests check the world behaves as designed (v2 breaks charge();
  renaming alone still fails on the env var; receipts fail without libxml2 and
  work after `apt-get install libxml2`; dev server not up at 2 s, up by 20 s).
- `newt/coordinator.py` + `newt/ledger.py` + `newt/ui.py`, `newt run <docs>`:
  pages run in parallel (4 workers by default, a thread each, fresh sandbox
  each); every state change goes to SQLite (`runs/newt.db`: runs, pages,
  causes, fixes, events); a rich live table shows each page's state, verdict,
  gate action, turns, time and last action. Fix candidates (gate action
  write_fix, or send_fix with the gate off) are grouped by root cause with one
  model call (`group_causes` tool); if the model's grouping does not cover
  every page exactly once, Newt groups by error signature instead (recorded as
  method "signature"); one candidate skips the call. A crash on one page is
  recorded and the run goes on; the daily-limit error stops the run, marks
  unstarted pages `not_run` and the run `stopped`. 4 new tests (scripted model,
  real Docker): grouping, the fallback, the honest stop, ledger round trip.
- `newt/fixer.py` (fixer + verifier, gate check c): for each root cause, one
  model call per page returns the whole corrected page (`write_page` tool),
  given the page, the failure evidence and the text of the source the worker
  cited (kept on the result for this). The fixed page goes to
  `runs/fixes/run-<id>/` (the docs folder is not touched), then a fresh worker
  follows it from zero in a fresh sandbox. Gate on: only a fix whose re-run
  passes is sent (`send_fix`); a failed re-run stays `write_fix`, unsent.
  Gate off (baseline): every fix is sent, and the re-run is still recorded so
  the baseline's wrong fixes can be counted. An empty/unchanged page from the
  fixer gets one retry, then is recorded as an error, never as a fix.
  4 new tests (scripted model, real Docker). Full suite: 42 tests; on one run 2
  failed because the sandbox could not reach the Debian mirror / PyPI (apt
  "Failed to fetch", 157 s); both passed on the re-run - network, not code.

## Demo

Not recorded yet.

## Known limits

- Newt cannot complete steps that need sign-ups, phone checks, captchas or
  paid plans; these are reported as "needs a human".

## Submission status

Not submitted.
