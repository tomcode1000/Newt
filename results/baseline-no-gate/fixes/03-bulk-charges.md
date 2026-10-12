<page>
# Charge customers from a CSV file

Run a batch of test charges from a spreadsheet export.

## 1. Set up

```bash
pip install "lumenpay>=2,<3"
export LUMEN_SECRET_KEY=sk_test_lumen_demo
```

## 2. Create the CSV

Save this as `charges.csv`:

```csv
customer,amount,currency
ada@example.com,1200,usd
grace@example.com,800,gbp
alan@example.com,15000,ngn
```

## 3. Write the batch script

Create `bulk_charge.py`:

```python
import csv

import lumen

total = 0
with open("charges.csv", newline="") as f:
    for row in csv.DictReader(f):
        payment = lumen.payments.create(
            amount=int(row["amount"]),
            currency=row["currency"],
            metadata={"customer": row["customer"]},
        )
        total += 1
        print(row["customer"], payment.status)

print(f"{total} charges done")
```

## 4. Run it

```bash
python bulk_charge.py
```

The last line should read `3 charges done`.

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md

</page>
