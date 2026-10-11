# lumen-sdk (demo library)

A tiny, fake payments SDK used as Newt's demo world. It makes no network
calls. Distribution name `lumenpay` (the name `lumen-sdk` is taken on PyPI),
import name `lumen`.

- `v1.0/` - `lumen.charge(amount, currency, ...)`; API key from `LUMEN_API_KEY`.
- `v2.0/` - `charge()` is gone, replaced by `lumen.payments.create(...)`; the key
  is read from `LUMEN_SECRET_KEY`. See `v2.0/CHANGELOG.md`.

Both versions ship `lumen.format_amount`, `lumen.receipts` (needs the libxml2
system library) and `lumen.devserver` (a local mock API that takes a few
seconds to start). Only `lumen/__init__.py`, `lumen/payments.py` (v2.0) and
the changelog differ between the two versions.

`demo/lumen_index.py` builds the wheels and serves them as a local package
index; see `demo/README.md`.
