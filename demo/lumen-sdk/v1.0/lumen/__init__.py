"""Lumen payments SDK v1.0 (Newt demo library; makes no network calls)."""

import os
import uuid
from dataclasses import dataclass, field

from ._money import format_amount

__version__ = "1.0.0"
__all__ = ["Charge", "AuthenticationError", "charge", "format_amount"]


class AuthenticationError(Exception):
    pass


@dataclass
class Charge:
    id: str
    amount: int
    currency: str
    status: str
    description: str | None = None
    metadata: dict = field(default_factory=dict)


def _api_key() -> str:
    key = os.environ.get("LUMEN_API_KEY")
    if not key:
        raise AuthenticationError("No API key provided. Set the LUMEN_API_KEY environment variable.")
    if not key.startswith("sk_test_"):
        raise AuthenticationError("Invalid API key: test keys start with sk_test_.")
    return key


def charge(amount: int, currency: str, description: str | None = None, metadata: dict | None = None) -> Charge:
    """Charge `amount` (in minor units, e.g. cents) in `currency`."""
    _api_key()
    if amount <= 0:
        raise ValueError("amount must be positive")
    format_amount(amount, currency)  # validates the currency
    return Charge(f"ch_{uuid.uuid4().hex[:16]}", amount, currency.lower(), "succeeded", description, metadata or {})
