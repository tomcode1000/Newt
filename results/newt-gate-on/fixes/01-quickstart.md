# Lumen quickstart

Take your first test payment with the Lumen Python SDK in two minutes.

## 1. Install the SDK

```bash
pip install lumenpay
```

## 2. Set your test API key

Every Lumen account comes with a test key. For this guide you can use the
shared sandbox key:

```bash
export LUMEN_SECRET_KEY=sk_test_lumen_demo
```

## 3. Charge a card

Create `first_charge.py`:

```python
import lumen

payment = lumen.payments.create(amount=1999, currency="usd", description="First test charge")
print(payment.id, payment.status)
```

Run it:

```bash
python first_charge.py
```

You should see an id starting with `pay_` followed by `succeeded`.

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md
