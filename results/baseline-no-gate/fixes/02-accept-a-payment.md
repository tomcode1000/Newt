# Accept a payment with metadata

Attach your own order data to a payment so you can match it up later.

## Before you start

Install the SDK and set your test key:

```bash
pip install lumenpay
export LUMEN_SECRET_KEY=sk_test_lumen_demo
```

## Charge with metadata

Create `checkout.py`:

```python
import lumen

order = {"order_id": "A-1001", "customer": "ada@example.com"}

charge = lumen.payments.create(
    amount=4250,
    currency="eur",
    description="Order A-1001",
    metadata=order,
)

print("charged", lumen.format_amount(charge.amount, charge.currency))
print("order", charge.metadata["order_id"], "->", charge.status)
```

Run it:

```bash
python checkout.py
```

Expected output:

```
charged €42.50
order A-1001 -> succeeded
```

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md
