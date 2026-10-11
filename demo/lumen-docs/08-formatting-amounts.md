# Formatting amounts

Lumen amounts are integers in minor units (cents, pence, kobo). Use
`lumen.format_amount` to show them to people.

## 1. Install

```bash
pip install lumenpay
```

## 2. Format some amounts

Create `amounts.py`:

```python
import lumen

for amount, currency in [(1999, "usd"), (50, "gbp"), (1250000, "ngn")]:
    print(lumen.format_amount(amount, currency))
```

Run it:

```bash
python amounts.py
```

Expected output:

```
$19.99
£0.50
₦12,500.00
```

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md
