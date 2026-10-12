"""Real tests against Docker (python:3.12-slim). Skipped if Docker isn't running."""

import time

import docker
import pytest

from newt.sandbox import Sandbox, docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker daemon not available"),
]


@pytest.fixture
def sb():
    with Sandbox() as sandbox:
        yield sandbox


def test_exec_success_and_failure(sb):
    ok = sb.exec("python --version")
    assert ok.ok and "Python 3.12" in ok.output
    bad = sb.exec("echo oops >&2; exit 3")
    assert bad.exit_code == 3 and not bad.ok and "oops" in bad.output
    missing = sb.exec("definitely-not-a-command")
    assert missing.exit_code == 127


def test_exec_timeout(sb):
    started = time.monotonic()
    r = sb.exec("echo before; sleep 30", timeout=2)
    assert r.timed_out and not r.ok
    assert "before" in r.output
    assert time.monotonic() - started < 15


def test_shell_state_persists_between_commands(sb):
    assert sb.exec("pwd").output.strip() == "/work"
    sb.exec("mkdir -p myproject && cd myproject")
    assert sb.exec("pwd").output.strip() == "/work/myproject"
    sb.exec("export LUMEN_KEY=abc123")
    assert sb.exec('echo "$LUMEN_KEY"').output.strip() == "abc123"
    # A failing command still keeps the state it reached.
    sb.exec("cd /tmp && false")
    assert sb.exec("pwd").output.strip() == "/tmp"


def test_venv_activation_persists(sb):
    assert sb.exec("python -m venv .venv && . .venv/bin/activate", timeout=120).ok
    assert sb.exec("which python").output.strip() == "/work/.venv/bin/python"


def test_write_and_read_file(sb):
    path = sb.write_file("app/config/settings.toml", 'name = "newt"\nemoji = "🦎"\n')
    assert path == "/work/app/config/settings.toml"
    assert sb.read_file("app/config/settings.toml") == 'name = "newt"\nemoji = "🦎"\n'
    assert sb.exec("cat app/config/settings.toml").output.startswith('name = "newt"')
    sb.exec("cd app")
    assert sb.read_file("config/settings.toml").startswith("name")  # relative to cwd
    with pytest.raises(FileNotFoundError):
        sb.read_file("/nope.txt")


def test_background_server_and_wait_for_port(sb):
    assert not sb.wait_for_port(8000, timeout=1)
    sb.write_file("www/index.html", "hello from newt")
    sb.exec("cd www")
    sb.start_background("python -m http.server 8000", name="web")
    assert sb.wait_for_port(8000, timeout=20)
    assert sb.background_alive("web")
    fetched = sb.exec("python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/index.html').read().decode())\"")
    assert "hello from newt" in fetched.output
    # The foreground shell is still usable and still in www/.
    assert sb.exec("pwd").output.strip() == "/work/www"
    assert "GET /index.html" in sb.background_logs("web")
    sb.stop_background("web")
    time.sleep(0.5)
    assert not sb.wait_for_port(8000, timeout=1)


def test_background_process_that_crashes(sb):
    sb.start_background("python -c 'import sys; print(\"boom\"); sys.exit(1)'", name="crash")
    time.sleep(1)
    assert not sb.background_alive("crash")
    assert "boom" in sb.background_logs("crash")


def test_each_sandbox_is_fresh():
    with Sandbox() as first:
        first.write_file("/work/leftover.txt", "x")
        assert first.exec("pip install --quiet --disable-pip-version-check six", timeout=120).ok
    with Sandbox() as second:
        assert second.exec("test -e /work/leftover.txt").exit_code == 1
        assert second.exec("python -c 'import six'").exit_code == 1


def test_teardown_removes_container():
    sb = Sandbox().start()
    name = sb.name
    client = docker.from_env()
    assert client.containers.get(name).status == "running"
    sb.teardown()
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(name)
    sb.teardown()  # idempotent


def test_failing_command_piped_into_tail_still_fails():
    # Run 4 (DeepSeek): `python first_charge.py 2>&1 | tail -40` hid an AttributeError behind tail's exit 0.
    with Sandbox() as sb:
        r = sb.exec("python -c 'raise SystemExit(3)' 2>&1 | tail -5")
        assert not r.ok
