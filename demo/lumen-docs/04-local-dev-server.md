# Run the Lumen dev server

The SDK ships a local mock of the Lumen API, so you can develop without
network access.

## 1. Install

```bash
pip install lumenpay
```

## 2. Start the dev server

In a separate terminal, run:

```bash
python -m lumen.devserver --port 8100
```

It loads its fixtures first, then prints
`Lumen dev server listening on http://127.0.0.1:8100`.

## 3. Check that it is up

```bash
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8100/health').read().decode())"
```

You should see `"status": "ok"` in the response.

---
Lumen SDK release notes: http://host.docker.internal:8765/lumenpay/CHANGELOG.md
