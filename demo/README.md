# Demo pages

`pages/notes-quickstart.md` — a small, correct quickstart. It has a background
server step (tests `wait_for_port`) and uses `curl`, which the slim sandbox
does not have: a good worker installs curl itself (its own setup) instead of
blaming the docs. Expected verdict: `pass`.

Run it (needs `ANTHROPIC_API_KEY` in `.env`):

    newt page demo/pages/notes-quickstart.md --out runs/notes-quickstart.json
