"""Per-docs-set settings, read from an optional `newt.json` next to the pages.

    {
      "sandbox_env":   {"PIP_INDEX_URL": "..."},          # env vars in every sandbox
      "fetch_rewrite": {"http://host.docker.internal:8765/": "http://127.0.0.1:8765/"}
    }

`fetch_rewrite` only changes where the host-side `fetch` tool connects; the
URL the model asked for (and may cite as cause_source) stays the same. It is
for hosts that the sandbox and the machine running Newt reach by different
names, like a local package index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable

from newt.sandbox import Sandbox
from newt.tools import http_fetch

CONFIG_NAME = "newt.json"


@dataclass
class DocsConfig:
    sandbox_env: dict[str, str] = field(default_factory=dict)
    fetch_rewrite: dict[str, str] = field(default_factory=dict)

    def sandbox_factory(self) -> Callable[[], Sandbox]:
        return partial(Sandbox, env=self.sandbox_env)

    def fetcher(self, fetch: Callable[[str], str] = http_fetch) -> Callable[[str], str]:
        rewrite = self.fetch_rewrite

        def fetch_rewritten(url: str) -> str:
            for prefix, target in rewrite.items():
                if url.startswith(prefix):
                    return fetch(target + url[len(prefix):])
            return fetch(url)

        return fetch_rewritten


def load_config(path: Path) -> DocsConfig:
    """Load newt.json from a docs directory (or the directory of a page). Missing -> defaults."""
    folder = path if path.is_dir() else path.parent
    config_file = folder / CONFIG_NAME
    if not config_file.is_file():
        return DocsConfig()
    data = json.loads(config_file.read_text(encoding="utf-8"))
    return DocsConfig(
        sandbox_env={str(k): str(v) for k, v in (data.get("sandbox_env") or {}).items()},
        fetch_rewrite={str(k): str(v) for k, v in (data.get("fetch_rewrite") or {}).items()},
    )
