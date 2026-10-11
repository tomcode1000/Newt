"""The lumen demo world behaves as the demo needs: v1.0 works, v2.0 breaks charge() and the env var,
receipts need libxml2, the dev server is slow to start. Real Docker + the local index on a spare port."""

import importlib.util
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from newt.config import load_config
from newt.sandbox import Sandbox, docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lumen_index", ROOT / "demo" / "lumen_index.py")
lumen_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lumen_index)

CHARGE = "python -c \"import lumen; c = lumen.charge(amount=1999, currency='usd'); print(c.id, c.status)\""
CREATE = "python -c \"import lumen; p = lumen.payments.create(amount=1999, currency='usd'); print(p.id, p.status)\""


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    lumen_index.build()
    state = tmp_path_factory.mktemp("index")
    lumen_index.PUBLISHED = state / "published.txt"
    lumen_index.STATE = state
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("0.0.0.0", port), lumen_index.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield port
    server.shutdown()


def sandbox(port):
    return Sandbox(env={"PIP_INDEX_URL": f"http://host.docker.internal:{port}/simple/", "PIP_TRUSTED_HOST": "host.docker.internal"})


def test_v1_charge_works_and_v2_breaks_it(index):
    lumen_index.publish(["1.0.0"], replace=True)
    with sandbox(index) as sb:
        assert sb.exec("pip install -q lumenpay", timeout=180).ok
        r = sb.exec("export LUMEN_API_KEY=sk_test_lumen_demo && " + CHARGE)
        assert r.ok and "succeeded" in r.output and r.output.startswith("ch_")

    lumen_index.publish(["2.0.0"])
    with sandbox(index) as sb:
        assert sb.exec("pip install -q lumenpay", timeout=180).ok
        assert "2.0.0" in sb.exec("python -c 'import lumen; print(lumen.__version__)'").output
        r = sb.exec("export LUMEN_API_KEY=sk_test_lumen_demo && " + CHARGE)
        assert not r.ok and "has no attribute 'charge'" in r.output
        # Renaming the call alone is not enough: the key moved to LUMEN_SECRET_KEY.
        r = sb.exec("export LUMEN_API_KEY=sk_test_lumen_demo && " + CREATE)
        assert not r.ok and "AuthenticationError" in r.output
        r = sb.exec("export LUMEN_SECRET_KEY=sk_test_lumen_demo && " + CREATE)
        assert r.ok and r.output.startswith("pay_")


def test_receipts_need_libxml2_and_dev_server_is_slow(index):
    lumen_index.publish(["2.0.0"], replace=True)
    with sandbox(index) as sb:
        assert sb.exec("pip install -q lumenpay", timeout=180).ok
        receipt = "python -c \"from lumen import receipts; print(receipts.render('x', 2599, 'usd'))\""
        r = sb.exec(receipt)
        assert not r.ok and "libxml2" in r.output
        assert sb.exec("apt-get update -qq && apt-get install -y -qq libxml2 >/dev/null", timeout=300).ok
        r = sb.exec(receipt)
        assert r.ok and "<amount>$25.99</amount>" in r.output

        sb.start_background("python -m lumen.devserver --port 8100")
        assert not sb.wait_for_port(8100, timeout=2)
        assert sb.wait_for_port(8100, timeout=20)


def test_changelog_follows_newest_published_version(index):
    import httpx

    lumen_index.publish(["1.0.0"], replace=True)
    url = f"http://127.0.0.1:{index}/lumenpay/CHANGELOG.md"
    assert "2.0.0" not in httpx.get(url).text
    lumen_index.publish(["2.0.0"])
    assert "`lumen.charge()` has been removed" in httpx.get(url).text


def test_lumen_docs_config_rewrites_fetches_only():
    config = load_config(ROOT / "demo" / "lumen-docs")
    assert config.sandbox_env["PIP_INDEX_URL"].startswith("http://host.docker.internal:8765/")
    seen = []
    fetch = config.fetcher(lambda url: seen.append(url) or "ok")
    fetch("http://host.docker.internal:8765/lumenpay/CHANGELOG.md")
    fetch("https://pypi.org/pypi/lumenpay/json")
    assert seen == ["http://127.0.0.1:8765/lumenpay/CHANGELOG.md", "https://pypi.org/pypi/lumenpay/json"]
