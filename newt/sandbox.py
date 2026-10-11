"""Docker sandboxes: one fresh, empty container per docs-page run.

A newcomer's terminal keeps its working directory and exported variables from
one command to the next ("cd myproject", "source .venv/bin/activate"). Each
`exec` runs in a new bash, so the sandbox saves `pwd` and `export -p` after
every command and restores them before the next one.
"""

from __future__ import annotations

import io
import posixpath
import shlex
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass

import docker

DEFAULT_IMAGE = "python:3.12-slim"
WORKDIR = "/work"
MAX_OUTPUT_CHARS = 200_000

_STATE_DIR = "/tmp/.newt"
_CWD_FILE = f"{_STATE_DIR}/cwd"
_ENV_FILE = f"{_STATE_DIR}/env"
# Variables bash manages itself; restoring them would be wrong or fail.
_SKIP_VARS = "PWD|OLDPWD|SHLVL|_|HOSTNAME"

# Restore the saved shell state, run the command, then save the state again.
_PRELUDE = f"""mkdir -p {_STATE_DIR}
cd "$(cat {_CWD_FILE} 2>/dev/null || echo {WORKDIR})" 2>/dev/null || cd {WORKDIR}
[ -f {_ENV_FILE} ] && . {_ENV_FILE} 2>/dev/null
"""
_EPILOGUE = f"""
__newt_rc=$?
pwd > {_CWD_FILE}
export -p | grep -Ev '^declare -[a-zA-Z]*r|^declare -x ({_SKIP_VARS})(=|$)' > {_ENV_FILE}
exit $__newt_rc
"""


class SandboxError(RuntimeError):
    pass


@dataclass
class ExecResult:
    command: str
    exit_code: int | None
    output: str
    timed_out: bool
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class BackgroundProcess:
    name: str
    command: str
    pid: int
    log_path: str


class Sandbox:
    """A throwaway container. Use as a context manager so it is always removed."""

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        *,
        cpus: float = 2.0,
        mem_limit: str = "2g",
        network: bool = True,
        env: dict[str, str] | None = None,
        client: docker.DockerClient | None = None,
    ) -> None:
        self.image = image
        self.env = dict(env or {})
        self.cpus = cpus
        self.mem_limit = mem_limit
        self.network = network
        self._client = client
        self.container = None
        self.name = f"newt-{uuid.uuid4().hex[:10]}"
        self.background: dict[str, BackgroundProcess] = {}

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "Sandbox":
        if self.container is not None:
            raise SandboxError("sandbox already started")
        if self._client is None:
            self._client = docker.from_env()
        self.container = self._client.containers.run(
            self.image,
            command=["sleep", "infinity"],
            name=self.name,
            detach=True,
            working_dir=WORKDIR,
            nano_cpus=int(self.cpus * 1e9),
            mem_limit=self.mem_limit,
            network_disabled=not self.network,
            environment=self.env,
            labels={"newt": "sandbox"},
            init=True,  # reap background processes' zombies
        )
        return self

    def teardown(self) -> None:
        if self.container is None:
            return
        try:
            self.container.remove(force=True)
        except docker.errors.NotFound:
            pass
        finally:
            self.container = None
            self.background.clear()

    def __enter__(self) -> "Sandbox":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.teardown()

    def _require(self):
        if self.container is None:
            raise SandboxError("sandbox is not running; call start() first")
        return self.container

    # -- commands ----------------------------------------------------------

    def exec(
        self,
        cmd: str,
        timeout: float = 120,
        *,
        env: dict[str, str] | None = None,
        keep_state: bool = True,
    ) -> ExecResult:
        """Run a shell command and wait for it, killing it after `timeout` seconds."""
        container = self._require()
        script = f"{_PRELUDE}{cmd}{_EPILOGUE}" if keep_state else cmd
        argv = ["timeout", "-k", "5", str(max(1, int(timeout))), "bash", "-c", script]
        api = self._client.api
        exec_id = api.exec_create(container.id, argv, environment=env, workdir=WORKDIR)["Id"]

        chunks: list[bytes] = []
        failure: list[BaseException] = []

        def pump() -> None:
            try:
                for chunk in api.exec_start(exec_id, stream=True):
                    chunks.append(chunk)
            except BaseException as e:  # surfaced below
                failure.append(e)

        started = time.monotonic()
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        # The in-container `timeout` should fire first; this is the backstop.
        reader.join(timeout + 15)
        duration = time.monotonic() - started
        if failure:
            raise SandboxError(f"exec failed: {failure[0]}") from failure[0]

        output = _decode(b"".join(chunks))
        if reader.is_alive():
            return ExecResult(cmd, None, output, True, duration)
        exit_code = api.exec_inspect(exec_id)["ExitCode"]
        # 124: `timeout` sent TERM; 137: it had to KILL after the grace period.
        timed_out = exit_code in (124, 137) and duration >= timeout
        return ExecResult(cmd, exit_code, output, timed_out, duration)

    def start_background(self, cmd: str, name: str | None = None) -> BackgroundProcess:
        """Start a long-running command (a server, a watcher) and return at once."""
        name = name or f"bg{len(self.background) + 1}"
        if name in self.background:
            raise SandboxError(f"background process {name!r} already exists")
        log_path = f"{_STATE_DIR}/bg-{name}.log"
        # The inner shell restores state but must not save it: the step that
        # started the server shouldn't change the foreground shell's state.
        inner = f"{_PRELUDE}exec {cmd}" if not cmd.lstrip().startswith("exec ") else f"{_PRELUDE}{cmd}"
        launcher = (
            f"mkdir -p {_STATE_DIR}; "
            f"setsid nohup bash -c {shlex.quote(inner)} > {log_path} 2>&1 < /dev/null & echo $!"
        )
        result = self.exec(launcher, timeout=30, keep_state=False)
        try:
            pid = int(result.output.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise SandboxError(f"could not start background process: {result.output!r}")
        proc = BackgroundProcess(name, cmd, pid, log_path)
        self.background[name] = proc
        return proc

    def background_alive(self, name: str) -> bool:
        proc = self.background[name]
        return self.exec(f"kill -0 {proc.pid}", timeout=10, keep_state=False).exit_code == 0

    def background_logs(self, name: str, tail_chars: int = 20_000) -> str:
        proc = self.background[name]
        return self.read_file(proc.log_path)[-tail_chars:]

    def stop_background(self, name: str) -> None:
        proc = self.background.pop(name)
        # setsid made the pid a process-group leader: kill the whole group.
        self.exec(f"kill -TERM -- -{proc.pid} 2>/dev/null || kill -TERM {proc.pid}", timeout=10, keep_state=False)

    def wait_for_port(self, port: int, timeout: float = 30, host: str = "127.0.0.1") -> bool:
        """True once something accepts TCP connections on host:port inside the sandbox."""
        probe = (
            "import socket, sys, time\n"
            f"deadline = time.monotonic() + {float(timeout)}\n"
            "while time.monotonic() < deadline:\n"
            "    try:\n"
            f"        socket.create_connection(({host!r}, {int(port)}), 1).close(); sys.exit(0)\n"
            "    except OSError:\n"
            "        time.sleep(0.25)\n"
            "sys.exit(1)\n"
        )
        result = self.exec(f"python3 -c {shlex.quote(probe)}", timeout=timeout + 10, keep_state=False)
        return result.exit_code == 0

    # -- files -------------------------------------------------------------

    @property
    def cwd(self) -> str:
        result = self.exec(f"cat {_CWD_FILE} 2>/dev/null || echo {WORKDIR}", timeout=10, keep_state=False)
        return result.output.strip() or WORKDIR

    def _abspath(self, path: str) -> str:
        return posixpath.normpath(path if path.startswith("/") else posixpath.join(self.cwd, path))

    def write_file(self, path: str, content: str | bytes) -> str:
        """Write a file (relative paths resolve against the shell's cwd). Returns the absolute path."""
        container = self._require()
        abspath = self._abspath(path)
        data = content.encode("utf-8") if isinstance(content, str) else content
        parent = posixpath.dirname(abspath)
        mk = self.exec(f"mkdir -p {shlex.quote(parent)}", timeout=10, keep_state=False)
        if not mk.ok:
            raise SandboxError(f"cannot create {parent}: {mk.output.strip()}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(posixpath.basename(abspath))
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
        if not container.put_archive(parent, buf.getvalue()):
            raise SandboxError(f"could not write {abspath}")
        return abspath

    def read_file(self, path: str, max_bytes: int = 1_000_000) -> str:
        container = self._require()
        abspath = self._abspath(path)
        try:
            stream, _ = container.get_archive(abspath)
        except docker.errors.NotFound:
            raise FileNotFoundError(abspath)
        buf = io.BytesIO(b"".join(stream))
        with tarfile.open(fileobj=buf) as tar:
            member = tar.next()
            if member is None or not member.isfile():
                raise IsADirectoryError(abspath)
            data = tar.extractfile(member).read(max_bytes)
        return _decode(data)


def _decode(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        cut = len(text) - MAX_OUTPUT_CHARS
        text = f"[... {cut} chars truncated ...]\n" + text[-MAX_OUTPUT_CHARS:]
    return text


def docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False
