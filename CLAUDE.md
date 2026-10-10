# CLAUDE.md

This repository is for Newt, a docs-validation agent for developer quickstarts.

## Mission

Newt verifies that project documentation actually works from a clean machine. The system reads docs, executes each step in a fresh sandbox, diagnoses failures, fixes broken instructions, and verifies the repaired flow before reporting back.

The key idea is simple: keep the docs and code in sync by testing real user journeys rather than trusting the README alone.

## Operating constraints

- Stay focused on Newt itself. Do not drift into a generic coding-agent, issue-fixer, or docs-site generator project.
- Treat this as a product built around docs reliability and validation, not a general-purpose automation script.
- Prefer exact, testable fixes over broad refactors.
- When a docs step fails, identify whether the break is in the environment, the code, or the page instructions before making changes.

## Primary stack

- Python 3.12
- Docker-based sandboxing
- Claude Code + Entire checkpoints
- SQLite for run state / ledger
- GitHub Actions for PR-driven validation

## Repo intent

This repo is expected to contain:

- the Newt CLI and worker/coordinator architecture
- sandboxed execution for docs pages
- root-cause analysis and doc repair flow
- fix verification in fresh containers
- optional PR automation and nightly checks

## Claude Code workflow

Use this sequence when working in the repo:

1. Update the smallest relevant component.
2. Validate behavior with the narrowest realistic command.
3. If a documentation change is needed, ensure it matches the actual execution flow.
4. Keep the fix grounded in the user journey: "clean environment -> run docs -> verify success".
5. When the issue is caused by changed library behavior, check the project context and dependency history before patching the docs.

## Important product context

Newt is designed to catch the exact failure mode where a product's docs drift from its code. The user's goal is not to build a clever framework; it is to help teams keep their quickstarts honest and working.

The project should feel like a disciplined QA and docs-repair system for developer onboarding.
