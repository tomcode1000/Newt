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

## Demo

Not recorded yet.

## Known limits

- Newt cannot complete steps that need sign-ups, phone checks, captchas or
  paid plans; these are reported as "needs a human".

## Submission status

Not submitted.
