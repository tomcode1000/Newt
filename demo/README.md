# Demo pages

`pages/notes-quickstart.md` — a small, correct quickstart. It has a background
server step (tests `wait_for_port`) and uses `curl`, which the slim sandbox
does not have: a good worker installs curl itself (its own setup) instead of
blaming the docs. Expected verdict: `pass`.

Run it (needs `OPENROUTER_API_KEY` in `.env`):

    newt page demo/pages/notes-quickstart.md --out runs/notes-quickstart.json

# The lumen demo world

A fake payments SDK, its docs, and a local package index, so a breaking
release can be staged on demand.

- `lumen-sdk/` — the library (`pip install lumenpay`, `import lumen`), in two
  versions. v1.0 has `lumen.charge()` and reads `LUMEN_API_KEY`. v2.0 removes
  `charge()` in favour of `lumen.payments.create()` and reads
  `LUMEN_SECRET_KEY` instead (see `lumen-sdk/v2.0/CHANGELOG.md`).
- `lumen_index.py` — builds both wheels and serves a PEP 503 index on port
  8765, plus the newest published changelog at `/lumenpay/CHANGELOG.md`. Any
  other project is redirected to PyPI.
- `lumen-docs/` — 8 pages written against v1.0, and `newt.json` (sandbox pip
  index + a fetch rewrite for the host).

| Page | What it exercises | Expected against v2.0 |
| --- | --- | --- |
| 01-quickstart | `charge()` + `LUMEN_API_KEY` | docs_broken (v2.0 rename) |
| 02-accept-a-payment | `charge()` + metadata | docs_broken (same cause) |
| 03-bulk-charges | `charge()` in a CSV loop | docs_broken (same cause) |
| 04-local-dev-server | background server that takes ~4 s to listen | pass (wait for the port) |
| 05-go-live | SMS phone verification | needs_human |
| 06-receipts | needs the libxml2 system library | pass (install it; not a docs fault) |
| 07-install | venv + install + version | pass |
| 08-formatting-amounts | `format_amount` | pass |

Run it:

    python demo/lumen_index.py serve --publish 1.0.0     # terminal 1
    python demo/lumen_index.py publish 2.0.0             # release v2.0
    newt page demo/lumen-docs/01-quickstart.md
