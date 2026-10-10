# Notes API — Quickstart

Get a tiny notes API running locally in under five minutes.

## 1. Create a project

```bash
mkdir notes-api && cd notes-api
python -m venv .venv
source .venv/bin/activate
```

## 2. Install Flask

```bash
pip install flask
```

## 3. Write the app

Create `app.py`:

```python
from flask import Flask, jsonify, request

app = Flask(__name__)
notes = []


@app.get("/notes")
def list_notes():
    return jsonify(notes)


@app.post("/notes")
def add_note():
    notes.append(request.get_json()["text"])
    return jsonify(notes), 201
```

## 4. Start the server

In a separate terminal (with the virtual environment active), run:

```bash
flask --app app run --port 5000
```

## 5. Try it

```bash
curl -X POST http://127.0.0.1:5000/notes -H "Content-Type: application/json" -d '{"text": "hello"}'
curl http://127.0.0.1:5000/notes
```

You should see `["hello"]`.
