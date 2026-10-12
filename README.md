# Newt — every doc, tried by a newcomer

> Coding agents ship code faster than anyone updates the docs. Newt follows
> your docs like a brand-new user, on a fresh machine, and fixes them only
> when it can prove the docs are wrong.

Built for neatHack (neatlogs), 10–12 Oct 2026. Team: topspot.

## The problem

A coding agent renames a function. The tests pass and the PR merges. The
README and the quickstart still show the old name. The next developer who
follows them hits an error on step 3, and most of them don't file a bug —
they leave. Nobody on the team notices, because nobody runs their own
quickstart from an empty machine.

An agent that "fixes the docs" on every failure makes this worse in a new
way: plenty of failures are not the docs' fault (a network blip, a step only
a person can do, the agent's own mistake), and maintainers already ignore AI
fixes they can't trust.

## What Newt does

For every page in a docs folder, in parallel:

1. **Fresh sandbox.** Starts a clean `python:3.12-slim` Docker container.
2. **Follow exactly.** Turns the page's prose into steps and runs each one as
   written — shell commands, files, background servers, waiting on ports.
3. **Whose fault?** When a step fails, the worker decides:

   | Verdict | What Newt does |
   | --- | --- |
   | its own mistake (typo, missing setup any reader would have) | fixes its setup and retries |
   | glitch (timeout, registry 5xx, rate limit) | waits and runs the step again |
   | needs a human (sign-up, phone check, payment) | marks it and moves on |
   | unsure | **sends nothing**, flags it for a person |
   | the docs are wrong | goes through the gate below |

4. **The docs-fault gate.** The model's opinion is not enough. A docs fix is
   written only when the evidence says so:
   - **a. reproduces** — the failing step is replayed in a *fresh* sandbox and
     fails the same way;
   - **b. explained** — a source fetched during the run (changelog, release
     notes, diff) backs up the cause.

   It is **sent** only after:
   - **c. verified** — the fixed page is followed from zero in another fresh
     sandbox and passes.

   Anything missing a or b becomes `unsure`. Code: [`newt/worker.py`](newt/worker.py)
   (`_gate`), [`newt/fixer.py`](newt/fixer.py).
5. **Root causes, not pages.** A coordinator groups failures by cause (three
   pages, one rename → one fix), sends each cause to the fixer, and runs a
   fresh verifier on every fixed page. State lives in a SQLite ledger.

```
docs folder ──► coordinator ──► 4 workers (one fresh container per page)
                    │                 │ verdict + evidence
                    │◄────────────────┘
                    ├─ group by root cause
                    ├─ gate: reproduces? explained?
                    ├─ fixer rewrites pages for each cause
                    └─ verifier re-runs each fixed page from zero ──► sent / held back
```

## Results

Demo world: a small payments SDK, `lumenpay`. Eight docs pages were written for
1.0; version 2.0 renamed `charge()` to `payments.create()` and `LUMEN_API_KEY`
to `LUMEN_SECRET_KEY`. Both runs used the same 8 pages, the same model
(`deepseek-flash`), one run each. The "fix-everything" baseline is the same
agent with the gate turned off (`--no-gate`).

| | fixes sent | fixes that failed their re-run | real breaks fixed |
| --- | --- | --- | --- |
| Fix-everything baseline | 4 | 1 | 3 |
| **Newt (gate on)** | **2** | **0** | **2** |

- The baseline's failed fix was on `05-go-live`: the page's real blocker is a
  phone verification, which no docs edit can fix. Newt marked it `unsure` and
  sent nothing.
- Newt also held back one real break (`02-accept-a-payment`) as `unsure`: the
  failed command was logged under a different step number than the one the
  model cited, so check (a) could not replay it. It went to a person instead
  of being fixed — a miss, but not a wrong fix.
- The two fixes Newt sent (`01-quickstart`, `03-bulk-charges`) were grouped as
  one root cause and both passed a re-run from zero.

Raw output: [`results/baseline-no-gate/`](results/baseline-no-gate/) and
[`results/newt-gate-on/`](results/newt-gate-on/) (run JSON, log, and every
fixed page). This is 8 pages and one run each — evidence, not a rate.

## Failures along the way (and what the agent and we did next)

From [hackathon.md](hackathon.md), all found in real runs:

- **A piped failure looked like a success.** The model ran
  `python first_charge.py 2>&1 | tail -40`; the pipe's exit code was `tail`'s
  0, so the gate had no failed step to replay and demoted every real break to
  `unsure`. Fixed: sandbox commands run with `set -o pipefail` (+ test).
- **The run froze.** A py-spy dump showed all four workers deep in neatlogs'
  span-input serializer, walking a shared client object. Fixed by giving
  traced objects compact `to_dict()` forms (+ regression test).
- **A false alarm the gate caught.** On `06-receipts` (needs the system
  library `libxml2`) one run's model blamed the docs. The cited changelog quote
  didn't hold up, so the gate demoted it to `unsure` — no fix written.
- **Rate limits and an error disguised as success.** OpenRouter returned
  `provider_unavailable` with HTTP 200; it is now retried like a 502. Free-tier
  daily limits stop the run with a clear message instead of a fake result.

## Built with the neatHack tools

- **neatlogs** — every model call, tool call, worker run and gate decision
  (`docs_fault_gate` span) is traced. Traces are how the freeze and the
  pipefail bug above were found.
- **Entire** — the repo is Entire-enabled; coding-agent sessions are
  checkpointed against commits. Example checkpoint
  (`01M4NR81M3EWCT828WRH5FVVM7`) on the fixer/verifier commit:
  https://entire.io/gh/tomcode1000/Newt/commit/15f3f8675ab039bedfa361d1ab6cb374ddf9ce2a
  Honest note: the first 7 commits (10 Oct) have no checkpoints — the Entire
  CLI wasn't on PATH for that session.
- **cfo.ai** — business plan made with cfo.ai (pricing, cost per customer, 12-month forecast, inputs): [docs/newt-business-plan.xlsx](docs/newt-business-plan.xlsx). Revenue figures are a forecast, not customers.

## Run it

Needs Python 3.12+ and Docker.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env    # add a model key (below) and NEATLOGS_API_KEY for tracing
```

One page:

```bash
newt page demo/pages/notes-quickstart.md
```

The demo world (4 pages in parallel, root causes, gate, verified fixes):

```bash
python demo/lumen_index.py serve --publish 1.0.0 &   # the demo package index
python demo/lumen_index.py publish 2.0.0             # ship the breaking release
newt run demo/lumen-docs --out runs/v2.json          # add --no-gate for the baseline
```

State goes to `runs/newt.db`; fixed pages to `runs/fixes/`.

**Models.** Calls go through [OpenRouter](https://openrouter.ai) — set
`NEWT_MODEL` in `.env` (default: the free `nvidia/nemotron-3-super-120b-a12b:free`).
For DeepSeek's own API (used for the results above) set
`NEWT_MODEL=deepseek-flash` and `DEEPSEEK_API_KEY`. Newt backs off on rate
limits and stops with a clear message if a daily limit runs out.

## Limits

- Python docs pages only, run in Linux containers.
- Steps that need sign-ups, phone checks, captchas or paid plans are reported
  as "needs a human", never faked.
- Not built yet: PR mode (fix commit + comment on the PR that broke the docs)
  and a GitHub Action. The fix pages it would push are what `runs/fixes/` holds.

## Prior art

[quickstarted](https://pypi.org/project/quickstarted/) already runs an agent in
a Docker sandbox to try a quickstart against a hand-written success script and
reports pass/fail. Newt's focus is different: decide whose fault a failure is,
fix only what it can prove, verify the fix from zero, and group pages by root
cause. [Doc Detective](https://doc-detective.com/) runs hand-written
deterministic doc tests.

## License

MIT — see [LICENSE](LICENSE).
