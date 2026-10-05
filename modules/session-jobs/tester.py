#!/usr/bin/env python3
"""
agent-array session `tester` sidecar: runs the UNTRUSTED install + test command.

Why a separate container: the project's install hooks, its tests and anything the
models wrote are code nobody reviewed. The orchestrator container holds TASK_TOKEN
and LITELLM_KEY; this one holds no secret at all (no secret env, no secret mounts,
no ServiceAccount token, its own PID namespace so the orchestrator's
/proc/<pid>/environ is not visible). It shares exactly two volumes with the pod:

  /workspace  (READ-ONLY here)  orchestrator's tree: work/ + .ipc/ requests
  /testbox    (read-write here; READ-ONLY in the orchestrator)  copies + results

Protocol (plain files, polled; no sockets):
  orchestrator -> /workspace/.ipc/req-<n>.json
                  {"id": n, "nonce": hex, "cmd": "<shell command>", "timeout": seconds}
  tester       -> /testbox/results/res-<n>.json   (atomic rename)
                  {"id": n, "nonce": hex, "exit": int|null, "timed_out": bool,
                   "output": "<tail of stdout+stderr>", "seconds": float}
  tester       -> /testbox/.tester-ready          once, at start

Each run copies /workspace/work to /testbox/runs/<n>/src (tests never touch the
reviewed tree, so nothing they do can change the bundle), then runs the command
with a minimal env in a NEW SESSION (own process group). When it exits or times
out the whole group is SIGKILLed and, because this supervisor is PID 1 of the
container, so is every other process left in the container (setsid escapees
included). Nothing outlives a run. The nonce lets the orchestrator ignore any
result file planted by an earlier run.

Package installs go to the in-cluster READ-ONLY mirror (modules/pkg-mirror), the
only package source the pod can reach (networkpolicy.yaml has no internet egress).
NPM_REGISTRY / PIP_INDEX_URL in this container's env (task-job.template.yaml) are
exported to every run as npm_config_registry / PIP_INDEX_URL (+ PIP_TRUSTED_HOST for
an http index), so a custom TEST_CMD that runs a bare `npm install` or `pip install`
uses the mirror too. Environment variables outrank a project's .npmrc / pip.conf.

Runs as the container command (PID 1): python3 /usr/local/bin/tester.py
On SIGTERM (pod shutdown) it kills everything and wipes its scratch volumes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
TESTBOX = Path(os.environ.get("TESTBOX", "/testbox"))
IPC = WORKSPACE / ".ipc"
WORK = WORKSPACE / "work"
RESULTS = TESTBOX / "results"
RUNS = TESTBOX / "runs"
HOME = TESTBOX / "home"
TMP = Path(os.environ.get("TMPDIR", "/tmp"))
OUTPUT_TAIL = 64 * 1024
POLL = 0.25
REQ_RE = re.compile(r"req-(\d{1,6})\.json")


def _registry_url(name: str) -> str:
    """A package-registry URL from this container's env, or "" when unset/invalid:
    http(s), a host, no credentials, no whitespace (never logged with a secret)."""
    url = os.environ.get(name, "").strip()
    if not url:
        return ""
    try:
        u = urlsplit(url)
        ok = (u.scheme in ("http", "https") and bool(u.hostname) and u.username is None
              and u.password is None and not re.search(r"\s", url))
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"invalid package mirror setting: {name}")
    return url


def mirror_env() -> dict:
    """Env for every test run that points npm and pip at the package mirror."""
    env = {}
    npm = _registry_url("NPM_REGISTRY")
    if npm:
        env["npm_config_registry"] = npm
    pip = _registry_url("PIP_INDEX_URL")
    if pip:
        env["PIP_INDEX_URL"] = pip
        if urlsplit(pip).scheme == "http":
            # pip refuses a plain-http index unless its host is trusted; the
            # mirror is cluster-internal (the NetworkPolicy is the boundary).
            env["PIP_TRUSTED_HOST"] = urlsplit(pip).hostname
    return env


MIRROR_ENV = mirror_env()


class Terminated(BaseException):
    """SIGTERM: a BaseException so no `except Exception` around a run swallows it."""


def log(msg: str) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "component": "session-jobs", "event": "tester.progress",
        "actor": {"user": None, "sa": None, "sub": None}, "team": None,
        "target": {}, "outcome": "allow", "detail": {}}), flush=True)


def _on_term(_signo, _frame):
    raise Terminated()


def _reap() -> None:
    """PID 1 duty: collect orphans that were re-parented to us."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _kill_everything(pgid: int | None) -> None:
    if pgid:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if os.getpid() == 1:
        # kill(-1) from PID 1: every process in this PID namespace except us.
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.05)
    _reap()


def _rmtree(p: Path) -> None:
    def onexc(func, path, _exc):
        # Test code may chmod things read-only; we own them, so make them writable.
        try:
            os.chmod(os.path.dirname(path), stat.S_IRWXU)
            os.chmod(path, stat.S_IRWXU)
            func(path)
        except OSError:
            pass
    if p.is_symlink() or p.is_file():
        p.unlink(missing_ok=True)
    elif p.exists():
        shutil.rmtree(p, onexc=onexc)   # session-runner image is Python 3.12+


def _wipe(d: Path) -> None:
    if d.is_dir():
        for child in d.iterdir():
            _rmtree(child)


def _write_result(res: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS / f".res-{res['id']}.tmp"
    tmp.write_text(json.dumps(res))
    os.replace(tmp, RESULTS / f"res-{res['id']}.json")


def _load_request(path: Path, n: int) -> dict | None:
    try:
        req = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not (isinstance(req, dict) and req.get("id") == n
            and isinstance(req.get("nonce"), str) and re.fullmatch(r"[0-9a-f]{16,64}", req["nonce"])
            and isinstance(req.get("cmd"), str) and 0 < len(req["cmd"]) <= 8192
            and isinstance(req.get("timeout"), int) and 1 <= req["timeout"] <= 7200):
        return None
    return req


def run_one(req: dict) -> dict:
    n, started = req["id"], time.monotonic()
    (RESULTS / f"res-{n}.json").unlink(missing_ok=True)     # never trust a pre-existing result
    _rmtree(RUNS)
    src = RUNS / str(n) / "src"
    src.parent.mkdir(parents=True)
    shutil.copytree(WORK, src, symlinks=True)
    HOME.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(HOME), "TMPDIR": str(TMP),
        "LANG": "C.UTF-8", "CI": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1",
        "npm_config_update_notifier": "false", "npm_config_fund": "false",
        "npm_config_audit": "false", **MIRROR_ENV,
    }
    tail = bytearray()

    def pump(stream):
        for chunk in iter(lambda: stream.read(8192), b""):
            tail.extend(chunk)
            if len(tail) > OUTPUT_TAIL:
                del tail[:len(tail) - OUTPUT_TAIL]

    p = subprocess.Popen(["/bin/bash", "-c", req["cmd"]], cwd=src, env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    reader = threading.Thread(target=pump, args=(p.stdout,), daemon=True)
    reader.start()
    timed_out, code = False, None
    try:
        code = p.wait(timeout=req["timeout"])
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        _kill_everything(p.pid)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=5)
        if not reader.is_alive():
            p.stdout.close()
        _rmtree(RUNS)
    return {"id": n, "nonce": req["nonce"], "exit": None if timed_out else code,
            "timed_out": timed_out, "output": tail.decode("utf-8", "replace"),
            "seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (TESTBOX / ".tester-ready").write_text(f"{os.getpid()} {time.time()}\n")
    log(f"ready (pid {os.getpid()}, workspace read-only: {not os.access(WORKSPACE, os.W_OK)})")
    log("package mirror: npm=" + (MIRROR_ENV.get("npm_config_registry") or "(unset)")
        + " pip=" + (MIRROR_ENV.get("PIP_INDEX_URL") or "(unset)"))
    last = 0
    try:
        while True:
            _reap()
            pending = []
            if IPC.is_dir():
                for f in IPC.iterdir():
                    m = REQ_RE.fullmatch(f.name)
                    if m and int(m.group(1)) > last:
                        pending.append(int(m.group(1)))
            if not pending:
                time.sleep(POLL)
                continue
            n = min(pending)
            req = _load_request(IPC / f"req-{n}.json", n)
            last = n
            if req is None:
                log(f"request {n}: malformed, ignored")
                continue
            log(f"run {n}: {req['cmd'][:200]} (timeout {req['timeout']}s)")
            try:
                res = run_one(req)
            except Exception as exc:  # noqa: BLE001 -- report, never die mid-task
                _kill_everything(None)
                res = {"id": n, "nonce": req["nonce"], "exit": None, "timed_out": False,
                       "output": f"[tester] could not run the tests: {type(exc).__name__}: {exc}",
                       "seconds": 0.0}
            _write_result(res)
            log(f"run {n}: exit={res['exit']} timed_out={res['timed_out']} in {res['seconds']}s")
    except Terminated:
        log("SIGTERM: killing every test process and wiping scratch")
        _kill_everything(None)
        _wipe(TESTBOX)
        _wipe(TMP)
        return 0


if __name__ == "__main__":
    sys.exit(main())
