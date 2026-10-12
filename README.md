# Newt — every doc, tried by a newcomer

> Coding agents ship code faster than anyone updates the docs. Newt follows
> your docs like a brand-new user and fixes them before anyone gets stuck.

**Status: work in progress** — being built during neatHack (10–12 Oct 2026).
This README is a placeholder; setup instructions, architecture, bench numbers
and demo links will land here as they exist.

## What it does (planned)

1. Starts a fresh, empty sandbox (Docker) for each docs page.
2. Follows the page exactly as written — shell commands, files, servers.
3. When a step fails, decides whose fault it is: Newt's own sandbox, the docs,
   something only a human can do, or a transient glitch.
4. Only when the evidence shows the docs are wrong (the failure reproduces and
   the history explains why) does it write a fix — then re-runs the whole page
   from zero to prove the fix works.

## Setup

Needs Python 3.12+ and Docker.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # add OPENROUTER_API_KEY (and NEATLOGS_API_KEY for tracing)
newt page demo/pages/notes-quickstart.md
```

To check a whole docs folder (4 pages in parallel, failures grouped by root
cause, each fix re-run from zero before it counts):

```bash
python demo/lumen_index.py serve --publish 1.0.0 &   # the demo world's package index
python demo/lumen_index.py publish 2.0.0             # ship the breaking release
newt run demo/lumen-docs --out runs/v2.json          # add --no-gate for the fix-everything baseline
```

State goes to a SQLite ledger (`runs/newt.db`); fixed pages to `runs/fixes/`.

Models are called through [OpenRouter](https://openrouter.ai). Set `NEWT_MODEL`
in `.env` to choose one; the default is the free
`nvidia/nemotron-3-super-120b-a12b:free`. Free models have per-minute and
per-day request limits: Newt backs off on rate limits and stops with a clear
message if the daily limit is used up. For DeepSeek's own API, set
`NEWT_MODEL=deepseek-flash` and `DEEPSEEK_API_KEY` (the results below used it).

## First result

Demo world: 8 docs pages written for lumenpay 1.0, run against 2.0 (renamed
`charge()`, renamed env var), model `deepseek-flash`, one run each:

| | fixes sent | wrong fixes sent | verified by a re-run from zero |
| --- | --- | --- | --- |
| Fix-everything baseline (`--no-gate`) | 4 | 1 | 3 of 4 |
| Newt (docs-fault gate on) | 2 | 0 | 2 of 2 |

Newt also held back one real break it could not prove (flagged for a person).
Details and failures along the way: [hackathon.md](hackathon.md).

## Prior art

[quickstarted](https://pypi.org/project/quickstarted/) already runs an agent in
a Docker sandbox to try a quickstart and reports pass/fail. Newt's focus is
different: find which pages a PR broke, say why, fix them, prove the fix from
zero, and put it on the PR. [Doc Detective](https://doc-detective.com/) runs
hand-written deterministic doc tests.

## License

MIT — see [LICENSE](LICENSE).
