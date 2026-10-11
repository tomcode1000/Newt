# Go live

Switch from test payments to real ones.

## 1. Verify your phone number

Live mode is locked until the account owner verifies a phone number. Open
https://dashboard.lumen.example/settings/verify, enter your mobile number,
and type in the 6-digit code we send you by SMS.

## 2. Copy your live key

Once verified, the dashboard shows a live key starting with `sk_live_`. Copy
it from **Developers → API keys**.

## 3. Use the live key

```bash
pip install lumenpay
export LUMEN_API_KEY=sk_live_your_key_here
python -c "import lumen; print(lumen.format_amount(100, 'usd'))"
```

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md
