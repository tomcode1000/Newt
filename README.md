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

## Prior art

[quickstarted](https://pypi.org/project/quickstarted/) already runs an agent in
a Docker sandbox to try a quickstart and reports pass/fail. Newt's focus is
different: find which pages a PR broke, say why, fix them, prove the fix from
zero, and put it on the PR. [Doc Detective](https://doc-detective.com/) runs
hand-written deterministic doc tests.

## License

MIT — see [LICENSE](LICENSE).
