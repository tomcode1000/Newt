"""neatlogs tracing + the OpenRouter client, with keys read from .env.

Keys come from the process environment or a `.env` file (environment wins).
If NEATLOGS_API_KEY is missing, Newt still runs; it just isn't traced.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import neatlogs

# Re-exported so other modules decorate with `@tracing.span(kind=...)`.
span = neatlogs.span

_tracing_on = False


def load_env(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables are never overwritten. Returns what was read.
    Looks in the current directory, then the repo root, if no path is given.
    """
    candidates = [Path(path)] if path else [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            values = _parse_env(candidate.read_text(encoding="utf-8-sig"))
            for key, value in values.items():
                os.environ.setdefault(key, value)
            return values
    return {}


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and value:
            values[key] = value
    return values


def init_tracing(workflow_name: str = "newt") -> bool:
    """Start neatlogs if NEATLOGS_API_KEY is set. Returns whether tracing is on."""
    global _tracing_on
    load_env()
    if _tracing_on:
        return True
    api_key = os.environ.get("NEATLOGS_API_KEY")
    if not api_key:
        return False
    neatlogs.init(api_key=api_key, workflow_name=workflow_name)
    _tracing_on = True
    return True


def tracing_enabled() -> bool:
    return _tracing_on


DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def model_name() -> str:
    """The OpenRouter model id for workers: NEWT_MODEL, else the free default."""
    load_env()
    return os.environ.get("NEWT_MODEL") or DEFAULT_MODEL


def model_client() -> Any:
    """An OpenRouter client, wrapped by neatlogs when tracing is on."""
    from openrouter import OpenRouter

    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    client = OpenRouter(api_key=api_key)
    return neatlogs.wrap(client) if _tracing_on else client


def flush() -> None:
    """Send buffered spans before exit. Safe to call when tracing is off."""
    if _tracing_on:
        neatlogs.flush()
