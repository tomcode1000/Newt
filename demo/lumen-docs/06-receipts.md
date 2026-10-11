# Save XML receipts

Generate an XML receipt for a payment, ready for your accounting system.

## 1. Install

```bash
pip install lumenpay
```

`lumen.receipts` uses the libxml2 system library, which ships with macOS and
most Linux desktops.

## 2. Write a receipt

Create `receipt.py`:

```python
from lumen import receipts

path = receipts.save("receipt.xml", payment_id="demo_0001", amount=2599, currency="usd")
print(open(path).read())
```

Run it:

```bash
python receipt.py
```

The output contains `<amount>$25.99</amount>`.

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md
