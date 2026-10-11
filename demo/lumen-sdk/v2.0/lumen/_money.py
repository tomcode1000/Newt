SYMBOLS = {"usd": "$", "eur": "€", "gbp": "£", "ngn": "₦"}


def format_amount(amount: int, currency: str) -> str:
    """Format an amount in minor units: format_amount(1999, "usd") -> "$19.99"."""
    currency = currency.lower()
    if currency not in SYMBOLS:
        raise ValueError(f"unsupported currency: {currency}")
    return f"{SYMBOLS[currency]}{amount // 100:,}.{amount % 100:02d}"
