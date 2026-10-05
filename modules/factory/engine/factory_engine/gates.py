"""Gate runners: one interface, three ways to run js/gate.js.

  LocalGateRunner    node on the host. Allowed only for gates that never execute repo code or
                     model output (doc_map: acorn + tsc type-check; assemble_docs; tsc_*).
                     test_gen is refused here.
  DockerGateRunner   one foreground offline container per request (--network none, read-only rootfs,
                     tmpfs /tmp, no capabilities, non-root, repo mounted read-only), requests via
                     `docker run --rm -i`. For local testing of every kind, including test_gen.
  TesterSidecarRunner the cluster path: the configured isolated tester-sidecar interface.
                     Requests are files (.ipc/req-<n>.json with a nonce; the command is a constant
                     built from validated ids, never from model output), results are files
                     (<testbox>/results/res-<n>.json). The gate request itself is written into the
                     work tree the tester copies (work/.factory/gate/<rid>.json) and the gate prints
                     its JSON on a sentinel line inside the tester's output tail.

Every runner bounds its own concurrency (the testers) with a semaphore and every request with a
timeout.
"""
from __future__ import annotations

import abc
import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from . import config, jsbridge
from .fsutil import hidden, is_loader_crash

SENTINEL = "@@FACTORY-GATE-RESULT@@"
EXECUTES_MODEL_OUTPUT = {"test_gen"}
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


async def _to_thread_complete(func, /, *args, **kwargs):
    """Do not let task cancellation orphan a subprocess-owning worker thread."""
    work = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await work
        raise


class GateRefused(Exception):
    pass


def parse_sentinel(output: str) -> dict | None:
    for line in reversed((output or "").splitlines()):
        if line.startswith(SENTINEL):
            try:
                return json.loads(line[len(SENTINEL):])
            except ValueError:
                return None
    return None


class GateRunner(abc.ABC):
    name = "abstract"
    isolated = False            # may run gates that execute model output

    def __init__(self, parallel: int = 3, scratch: Path | None = None):
        self.parallel = max(1, int(parallel))
        self.sem = asyncio.Semaphore(self.parallel)
        self.scratch = Path(scratch) if scratch else None
        self.repos: dict[str, Path] = {}
        self.calls = 0
        self.busy_s = 0.0

    async def start(self, repos: dict[str, Path]) -> None:
        self.repos = {k: Path(v) for k, v in repos.items()}

    async def close(self) -> None:
        return None

    def check_kind(self, kind: str) -> None:
        if kind in EXECUTES_MODEL_OUTPUT and not self.isolated:
            raise GateRefused(f"{kind} gates execute model output; the {self.name} runner is not isolated "
                              "(use --gate docker or --gate tester)")

    async def run(self, repo: str, request: dict, timeout: int = 600) -> dict:
        self.check_kind(request.get("kind", ""))
        if repo not in self.repos:
            return {"ok": False, "stage": "gate_env", "message": f"repo {repo} is not mounted in the gate runner"}
        async with self.sem:
            t0 = time.monotonic()
            try:
                return await self._call(repo, request, timeout)
            except GateRefused:
                raise
            except Exception as exc:  # noqa: BLE001 - an environment failure, never a model failure
                return {"ok": False, "stage": "gate_env", "message": f"{type(exc).__name__}: {str(exc)[:1500]}"}
            finally:
                self.calls += 1
                self.busy_s += time.monotonic() - t0

    @abc.abstractmethod
    async def _call(self, repo: str, request: dict, timeout: int) -> dict: ...

    def describe(self) -> dict:
        return {"runner": self.name, "parallel": self.parallel, "isolated": self.isolated}


# --------------------------------------------------------------------------- host
class LocalGateRunner(GateRunner):
    name = "local"
    isolated = False

    def __init__(self, parallel: int = 3, scratch: Path | None = None, *, deps: dict | None = None):
        super().__init__(parallel, scratch)
        self.deps = {k: Path(v) for k, v in (deps or {}).items()}

    async def start(self, repos: dict[str, Path]) -> None:
        await super().start(repos)
        jsbridge.check_tools()

    async def _call(self, repo: str, request: dict, timeout: int) -> dict:
        scratch = (self.scratch or Path(os.environ.get("TEMP", "/tmp"))) / "gates" / repo
        scratch.mkdir(parents=True, exist_ok=True)
        args = [jsbridge.node_bin(), str(config.JS_DIR / "gate.js"), "--base", str(self.repos[repo]),
                "--tools", str(jsbridge.tools_dir()), "--scratch", str(scratch),
                *(["--deps", str(self.deps[repo])] if repo in self.deps else [])]

        def go():
            return subprocess.run(args, input=json.dumps(request), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, env=jsbridge._env(),
                                  **hidden())
        for attempt in range(jsbridge.LOADER_RETRIES + 1):
            try:
                r = await _to_thread_complete(go)
            except subprocess.TimeoutExpired:
                return {"ok": False, "stage": "gate_env", "message": f"gate timed out after {timeout}s"}
            if is_loader_crash(r.returncode) and not r.stdout.strip() and attempt < jsbridge.LOADER_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))    # node died at start-up (desktop heap, DLL init)
                continue
            break
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {"ok": False, "stage": "gate_env",
                    "message": f"gate produced no JSON (exit {r.returncode}): {(r.stderr or r.stdout)[-1500:]}"}


# --------------------------------------------------------------------------- docker
def dpath(p: Path) -> str:
    """Windows path -> the form Docker Desktop accepts in -v (C:/Users/...)."""
    return str(Path(p).resolve()).replace("\\", "/")


def _docker(args: list[str], *, input: str | None = None, timeout: int = 600, check: bool = True):
    r = subprocess.run(["docker", *args], input=input, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, **hidden())
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed ({r.returncode}): {r.stderr[-1500:]}")
    return r


TOOLS_IMAGE = os.environ.get("FACTORY_TOOLS_IMAGE", "aa-factory-tools:1")


def ensure_tools_image(image: str = TOOLS_IMAGE, build: bool = True) -> str:
    r = _docker(["image", "inspect", image, "--format", "{{.Id}}"], check=False, timeout=60)
    if r.returncode == 0:
        return r.stdout.strip()
    if not build:
        raise RuntimeError(f"docker image {image} is missing; build it: docker build -t {image} "
                           f"-f {config.DOCKER_DIR / 'Dockerfile.tools'} {config.JS_DIR}")
    _docker(["build", "-q", "-t", image, "-f", dpath(config.DOCKER_DIR / "Dockerfile.tools"), dpath(config.JS_DIR)],
            timeout=1800)
    return _docker(["image", "inspect", image, "--format", "{{.Id}}"], timeout=60).stdout.strip()


class DockerGateRunner(GateRunner):
    name = "docker"
    isolated = True

    def __init__(self, parallel: int = 3, scratch: Path | None = None, *, tag: str = "", image: str = TOOLS_IMAGE,
                 cpus: str = "4", memory: str = "4g", deps: dict | None = None):
        super().__init__(parallel, scratch)
        self.deps = {k: Path(v) for k, v in (deps or {}).items()}
        self.tag = re.sub(r"[^a-z0-9-]", "-", tag.lower())[:24] or secrets.token_hex(4)
        self.image = image
        self.cpus, self.memory = cpus, memory
        self.containers: dict[str, str] = {}

    def _name(self, repo: str) -> str:
        return f"factory-gate-{self.tag}-{re.sub(r'[^a-z0-9-]', '-', repo.lower())}"[:63]

    async def start(self, repos: dict[str, Path]) -> None:
        await super().start(repos)
        await asyncio.to_thread(ensure_tools_image, self.image)

    async def close(self) -> None:
        for name in list(self.containers.values()):
            await asyncio.to_thread(_docker, ["rm", "-f", name], check=False)
        self.containers.clear()

    async def _call(self, repo: str, request: dict, timeout: int) -> dict:
        name = self._name(repo) + "-" + secrets.token_hex(4)
        self.containers[name] = name
        args = ["run", "--rm", "-i", "--name", name,
                "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,size=2g",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "1024",
                "--cpus", self.cpus, "--memory", self.memory, "--user", "1000:1000",
                "-e", "FACTORY_GATE_ISOLATED=1", "-e", "HOME=/tmp",
                "-e", "NODE_PATH=/opt/factory/tools/node_modules",
                "-v", f"{dpath(self.repos[repo])}:/work/base/{repo}:ro",
                *(["-v", f"{dpath(self.deps[repo])}:/work/deps/{repo}/node_modules:ro"] if repo in self.deps else []),
                "-v", f"{dpath(config.JS_DIR)}:/opt/factory/js:ro",
                self.image, "node", "/opt/factory/js/gate.js", "--base", f"/work/base/{repo}",
                "--tools", "/opt/factory/tools", "--scratch", "/tmp/gate",
                *(["--deps", f"/work/deps/{repo}/node_modules"] if repo in self.deps else [])]
        try:
            r = await _to_thread_complete(_docker, args, input=json.dumps(request), timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "gate_env", "message": f"gate timed out after {timeout}s"}
        finally:
            await asyncio.to_thread(_docker, ["rm", "-f", name], check=False)
            self.containers.pop(name, None)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return {"ok": False, "stage": "gate_env",
                    "message": f"gate produced no JSON (exit {r.returncode}): {(r.stderr or r.stdout)[-1500:]}"}


# --------------------------------------------------------------------------- tester sidecar
class TesterSidecarRunner(GateRunner):
    """File IPC with pipeline/job/tester.py sidecars.

    `workspace` is the tree the testers mount read-only (/workspace): it holds work/<repo>/... and
    .ipc/. `testers` is a list of {"ipc": <dir the tester polls>, "results": <its results dir>};
    tester v1 has one (/workspace/.ipc, /testbox/results); v2 adds a TESTER_ID subdir per tester.
    `remote_work` is how the tester sees work/ relative to its run copy (v1 copies work/ and runs
    the command inside the copy, so repos are at ./<repo>).
    """
    name = "tester"
    isolated = True

    def __init__(self, workspace: Path, testers: list[dict], *, js_in_image: str = "/opt/factory/js",
                 tools_in_image: str = "/opt/factory/tools", poll_s: float = 0.25, parallel: int | None = None,
                 slack_s: float = 120.0):
        super().__init__(parallel or len(testers))
        self.workspace = Path(workspace)
        self.testers = [{"ipc": Path(t["ipc"]), "results": Path(t["results"])} for t in testers]
        self.js = js_in_image
        self.tools = tools_in_image
        self.poll_s = poll_s
        self.slack_s = slack_s
        self._free: asyncio.Queue | None = None
        self._seq: dict[int, int] = {}

    async def start(self, repos: dict[str, Path]) -> None:
        await super().start(repos)
        self._free = asyncio.Queue()
        for i, t in enumerate(self.testers):
            t["ipc"].mkdir(parents=True, exist_ok=True)
            seen = [int(m.group(1)) for f in t["ipc"].glob("req-*.json") if (m := re.fullmatch(r"req-(\d+)\.json", f.name))]
            self._seq[i] = max(seen, default=0)
            self._free.put_nowait(i)
        (self.workspace / "work" / ".factory" / "gate").mkdir(parents=True, exist_ok=True)

    def command(self, repo: str, rid: str) -> str:
        if not _SAFE.fullmatch(repo) or not _SAFE.fullmatch(rid):
            raise GateRefused("unsafe repo or request id")
        # Constant command; the only variables are validated ids. Runs in the tester's copy of work/.
        return (f"FACTORY_GATE_ISOLATED=1 node {self.js}/gate.js --request .factory/gate/{rid}.json "
                f"--base {repo} --tools {self.tools} --scratch \"${{TMPDIR:-/tmp}}/factory-gate\" --sentinel")

    async def _call(self, repo: str, request: dict, timeout: int) -> dict:
        assert self._free is not None
        i = await self._free.get()
        owned: list[Path] = []
        try:
            t = self.testers[i]
            rid = f"g{int(time.time() * 1000):x}{secrets.token_hex(3)}"
            gdir = self.workspace / "work" / ".factory" / "gate"
            gfile = gdir / f"{rid}.json"
            tmp = gdir / f".{rid}.tmp"
            owned.extend((gfile, tmp))
            tmp.write_text(json.dumps(request), encoding="utf-8")
            os.replace(tmp, gfile)
            self._seq[i] += 1
            n, nonce = self._seq[i], secrets.token_hex(16)
            req = {"id": n, "nonce": nonce, "cmd": self.command(repo, rid), "timeout": int(max(1, min(timeout, 7200)))}
            rtmp = t["ipc"] / f".req-{n}.tmp"
            req_path = t["ipc"] / f"req-{n}.json"
            res_path = t["results"] / f"res-{n}.json"
            owned.extend((rtmp, req_path, res_path))
            rtmp.write_text(json.dumps(req), encoding="utf-8")
            os.replace(rtmp, req_path)
            end = time.monotonic() + timeout + self.slack_s
            res = None
            while time.monotonic() < end:
                try:
                    cand = json.loads(res_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cand = None
                # the nonce is only in our request: a result planted by an earlier run cannot match it
                if isinstance(cand, dict) and cand.get("id") == n and cand.get("nonce") == nonce:
                    res = cand
                    break
                await asyncio.sleep(self.poll_s)
            if res is None:
                return {"ok": False, "stage": "gate_env", "message": f"the tester gave no result within {timeout + self.slack_s:.0f}s"}
            if res.get("timed_out"):
                return {"ok": False, "stage": "gate_env", "message": f"the gate timed out in the tester after {timeout}s"}
            parsed = parse_sentinel(str(res.get("output", "")))
            if parsed is None:
                return {"ok": False, "stage": "gate_env",
                        "message": f"no gate result in the tester output (exit {res.get('exit')}): "
                                   f"{str(res.get('output', ''))[-1200:]}"}
            return parsed
        finally:
            for path in owned:
                try:
                    path.unlink()
                except OSError:
                    pass
            self._free.put_nowait(i)


def make_runner(kind: str, *, parallel: int, scratch: Path, tag: str = "", tester_cfg: dict | None = None,
                deps: dict | None = None) -> GateRunner:
    if kind == "local":
        return LocalGateRunner(parallel, scratch, deps=deps)
    if kind == "docker":
        if not shutil.which("docker"):
            raise RuntimeError("docker is not on PATH")
        return DockerGateRunner(parallel, scratch, tag=tag, deps=deps)
    if kind == "tester":
        if not tester_cfg:
            raise RuntimeError("--gate tester needs a tester config (workspace + testers)")
        return TesterSidecarRunner(Path(tester_cfg["workspace"]), tester_cfg["testers"],
                                   js_in_image=tester_cfg.get("js", "/opt/factory/js"),
                                   tools_in_image=tester_cfg.get("tools", "/opt/factory/tools"))
    raise ValueError(f"unknown gate runner {kind}")
