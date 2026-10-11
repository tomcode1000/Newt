"""Receipts as XML. Uses the libxml2 system library through ctypes."""

import ctypes
import ctypes.util
from html import escape

from ._money import format_amount


def _libxml2():
    path = ctypes.util.find_library("xml2")
    if not path:
        raise OSError(
            "lumen.receipts needs the libxml2 system library, which was not found. "
            "Install it with your system package manager (e.g. `apt-get install libxml2`, "
            "`brew install libxml2`)."
        )
    lib = ctypes.CDLL(path)
    lib.xmlInitParser()
    return lib


def render(payment_id: str, amount: int, currency: str) -> str:
    """Return an XML receipt for one payment."""
    _libxml2()
    return (
        '<?xml version="1.0"?>\n'
        f'<receipt id="{escape(payment_id)}">\n'
        f"  <amount>{escape(format_amount(amount, currency))}</amount>\n"
        "</receipt>\n"
    )


def save(path: str, payment_id: str, amount: int, currency: str) -> str:
    """Write the XML receipt to `path`."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(payment_id, amount, currency))
    return path
