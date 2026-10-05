"""Running the engine's own JS tools (factory/engine/js) on the host.

Only parse-only tools run on the host: the inventory (acorn) and the comment-only doc_map gate
(acorn + tsc type-check; neither executes repo code or model output). Anything that executes
model output (test_gen: jest) goes through an isolated runner (gates.DockerGateRunner or the
tester sidecar).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import JS_DIR
from .fsutil import hidden, is_loader_crash

LOADER_RETRIES = 2


class JsToolError(RuntimeError):
    pass


def node_bin() -> str:
    exe = os.environ.get("FACTORY_NODE") or shutil.which("node")
    if not exe:
        raise JsToolError("node is not on PATH (set FACTORY_NODE); the packer and the host gate need Node 20+")
    return exe


def tools_dir() -> Path:
    """Dir whose node_modules holds the pinned acorn/typescript (`npm ci` in factory/engine/js)."""
    return Path(os.environ.get("FACTORY_TOOLS", str(JS_DIR)))


def check_tools() -> dict:
    nm = tools_dir() / "node_modules"
    missing = [p for p in ("acorn", "acorn-walk", "typescript", "@types/node") if not (nm / p / "package.json").exists()]
    if missing:
        raise JsToolError(f"JS tools missing in {nm}: {', '.join(missing)}; run `npm ci --ignore-scripts` "
                          f"in {JS_DIR}")
    return {p: json.loads((nm / p / "package.json").read_text(encoding="utf-8"))["version"]
            for p in ("acorn", "acorn-walk", "typescript", "@types/node")}


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in (
        "PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA")}
    env["NODE_PATH"] = str(tools_dir() / "node_modules")
    env["NODE_OPTIONS"] = ""
    return env


def run_script(script: str, args: list[str], *, stdin: str | None = None, timeout: int = 300) -> str:
    cmd = [node_bin(), str(JS_DIR / script), *args]
    for attempt in range(LOADER_RETRIES + 1):
        try:
            r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=_env(), **hidden())
        except subprocess.TimeoutExpired as exc:
            raise JsToolError(f"{script} timed out after {timeout}s") from exc
        if is_loader_crash(r.returncode) and not r.stdout and attempt < LOADER_RETRIES:
            time.sleep(1.5 * (attempt + 1))     # node died at start-up (desktop heap, DLL init): try again
            continue
        break
    if r.returncode != 0:
        raise JsToolError(f"{script} failed (exit {r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
    return r.stdout


def inventory(repo_dir: Path, roots: list[str] | None = None, files: list[str] | None = None,
              timeout: int = 600) -> dict:
    args = ["--base", str(repo_dir)]
    if roots:
        args += ["--roots", ",".join(roots)]
    if files:
        args += ["--files", ",".join(files)]
    return json.loads(run_script("inventory.js", args, timeout=timeout))
