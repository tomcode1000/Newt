"""Local package index for the demo world (PEP 503 "simple" API).

    python demo/lumen_index.py serve --publish 1.0.0     # start with v1.0 only
    python demo/lumen_index.py publish 2.0.0             # release v2.0 (server keeps running)

Serves `lumenpay` wheels built from demo/lumen-sdk/v*/ and the changelog of
the newest published version at /lumenpay/CHANGELOG.md. Every other project
is redirected to PyPI, so a sandbox can use this as its only index
(PIP_INDEX_URL) and the real PyPI `lumen*` names can never shadow ours.

Sandboxes reach it at http://host.docker.internal:8765 (Docker Desktop).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SDK = HERE / "lumen-sdk"
STATE = HERE / "index"  # git-ignored: built wheels + the published list
WHEELS = STATE / "wheels"
PUBLISHED = STATE / "published.txt"
PROJECT = "lumenpay"
DEFAULT_PORT = 8765


def versions_available() -> dict[str, Path]:
    """version -> source dir, from demo/lumen-sdk/v*/pyproject.toml."""
    found = {}
    for pyproject in sorted(SDK.glob("v*/pyproject.toml")):
        m = re.search(r'^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
        if m:
            found[m.group(1)] = pyproject.parent
    return found


def build() -> None:
    WHEELS.mkdir(parents=True, exist_ok=True)
    for version, src in versions_available().items():
        if not list(WHEELS.glob(f"{PROJECT}-{version}-*.whl")):
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-q", "-w", str(WHEELS), str(src)],
                check=True,
            )
        print(f"built {PROJECT} {version}")


def published() -> list[str]:
    if not PUBLISHED.exists():
        return []
    return [v.strip() for v in PUBLISHED.read_text(encoding="utf-8").split() if v.strip()]


def publish(versions: list[str], replace: bool = False) -> None:
    known = versions_available()
    for v in versions:
        if v not in known:
            raise SystemExit(f"unknown version {v}; have {sorted(known)}")
    STATE.mkdir(parents=True, exist_ok=True)
    current = [] if replace else published()
    current += [v for v in versions if v not in current]
    PUBLISHED.write_text("\n".join(current) + "\n", encoding="utf-8")
    print(f"published: {', '.join(current)}")


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        live = published()
        if path in ("/simple", "/simple/"):
            return self._send(200, f'<a href="/simple/{PROJECT}/">{PROJECT}</a>\n', "text/html")
        m = re.fullmatch(r"/simple/([^/]+)/?", path)
        if m:
            name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
            if name != PROJECT:
                return self._redirect(f"https://pypi.org/simple/{name}/")
            links = []
            for wheel in sorted(WHEELS.glob(f"{PROJECT}-*.whl")):
                if wheel.name.split("-")[1] in live:
                    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
                    links.append(f'<a href="/files/{wheel.name}#sha256={digest}" data-requires-python="&gt;=3.10">{wheel.name}</a><br>')
            return self._send(200, "<html><body>\n" + "\n".join(links) + "\n</body></html>\n", "text/html")
        m = re.fullmatch(r"/files/([^/]+\.whl)", path)
        if m and (WHEELS / m.group(1)).is_file() and m.group(1).split("-")[1] in live:
            return self._send(200, (WHEELS / m.group(1)).read_bytes(), "application/octet-stream")
        if path == f"/{PROJECT}/CHANGELOG.md" and live:
            newest = max(live, key=_vkey)
            return self._send(200, (versions_available()[newest] / "CHANGELOG.md").read_bytes(), "text/markdown; charset=utf-8")
        return self._send(404, "not found\n", "text/plain")

    def _send(self, code: int, body: str | bytes, ctype: str) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, url: str) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("[lumen-index] " + (fmt % args) + "\n")


def serve(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"lumen index on http://0.0.0.0:{port}/simple/ (published: {', '.join(published()) or 'none'})", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="build wheels and serve the index")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--publish", nargs="*", default=None, help="versions published at start (replaces the list)")
    p = sub.add_parser("publish", help="publish versions on a running index")
    p.add_argument("versions", nargs="+")
    sub.add_parser("build", help="build wheels only")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        build()
    elif args.cmd == "publish":
        publish(args.versions)
    else:
        build()
        if args.publish is not None:
            publish(args.publish, replace=True)
        serve(args.port)


if __name__ == "__main__":
    main()
