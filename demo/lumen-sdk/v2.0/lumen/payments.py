"""Payments: lumen.payments.create(...)."""

import os
import uuid
from dataclasses import dataclass, field

from ._money import format_amount


class AuthenticationError(Exception):
    pass


@dataclass
class Payment:
    id: str
    amount: int
    currency: str
    status: str
    description: str | None = None
    metadata: dict = field(default_factory=dict)


def _api_key() -> str:
    key = os.environ.get("LUMEN_SECRET_KEY")
    if not key:
        raise AuthenticationError("No API key provided.")
    if not key.startswith("sk_test_"):
        raise AuthenticationError("Invalid API key: test keys start with sk_test_.")
    return key


def create(amount: int, currency: str, description: str | None = None, metadata: dict | None = None) -> Payment:
    """Create a payment of `amount` (in minor units, e.g. cents) in `currency`."""
    _api_key()
    if amount <= 0:
        raise ValueError("amount must be positive")
    format_amount(amount, currency)
    return Payment(f"pay_{uuid.uuid4().hex[:16]}", amount, currency.lower(), "succeeded", description, metadata or {})
