"""A local mock of the Lumen API for development: python -m lumen.devserver --port 8100

It loads fixtures for a few seconds before it starts listening.
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "version": __version__}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": "not found"}).encode()
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m lumen.devserver")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    print("Loading fixtures...", flush=True)
    time.sleep(4)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Lumen dev server listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
