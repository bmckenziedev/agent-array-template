"""Snapshot layout: the directory `aa snapshot up` delivers (/work/snap/<id>/ in the frontier pod):

    <dir>/MANIFEST.json          {"snapshot_id", "repos": [{"name", "commit", "tree", ...}], ...}
    <dir>/<repo>/...             one directory per repo (git HEAD blobs; no .git)

Also accepted, for local runs: a directory of repo copies without MANIFEST.json (base commits come
from `git rev-parse HEAD` when the copy is a git checkout), or a single repo directory (--repo NAME).
The factory never writes into the snapshot.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import hidden

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_isjunction = getattr(os.path, "isjunction", lambda _p: False)


def _is_link(path: Path) -> bool:
    try:
        reparse = bool(getattr(path.lstat(), "st_file_attributes", 0)
                       & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except (FileNotFoundError, OSError):
        reparse = False
    return path.is_symlink() or _isjunction(path) or reparse


def _safe_dir(path: Path, boundary: Path, *, direct: bool = False) -> Path | None:
    if _is_link(path) or not path.is_dir():
        return None
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(boundary)
    except ValueError:
        return None
    if direct and len(relative.parts) != 1:
        return None
    return resolved


@dataclass
class SnapRepo:
    name: str
    root: Path
    base_commit: str | None = None
    tree: str | None = None
    pins: dict = field(default_factory=dict)


@dataclass
class Snapshot:
    dir: Path
    snapshot_id: str | None
    repos: dict[str, SnapRepo]
    manifest: dict | None = None


def _git_head(root: Path) -> tuple[str | None, str | None]:
    try:
        c = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], capture_output=True,
                           text=True, timeout=30, **hidden())
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if c.returncode != 0:
        return None, None
    lines = c.stdout.split()
    return (lines[0] if lines else None), (lines[1] if len(lines) > 1 else None)


def load(path: Path, repo: str | None = None) -> Snapshot:
    given = Path(path)
    if _is_link(given):
        raise ValueError(f"snapshot dir {given} must not be a symlink or junction")
    d = given.resolve()
    if _safe_dir(d, d) is None:
        raise FileNotFoundError(f"snapshot dir {d} does not exist")
    mf = d / "MANIFEST.json"
    if _is_link(mf):
        raise ValueError(f"snapshot manifest {mf} must not be a symlink or junction")
    if mf.is_file():
        man = json.loads(mf.read_text(encoding="utf-8"))
        repos = {}
        for r in man.get("repos", []):
            name = r.get("name")
            root = d / name if isinstance(name, str) else d
            safe = _safe_dir(root, d, direct=True)
            if not name or not NAME_RE.fullmatch(name) or safe is None:
                continue
            repos[name] = SnapRepo(name, safe, r.get("commit"), r.get("tree"), r.get("pins") or {})
        return Snapshot(d, man.get("snapshot_id"), repos, man)
    if (d / "package.json").is_file():
        name = repo or d.name
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"bad repo name {name!r}")
        c, t = _git_head(d)
        return Snapshot(d.parent, None, {name: SnapRepo(name, d, c, t)})
    repos = {}
    for sub in sorted(p for p in d.iterdir() if NAME_RE.fullmatch(p.name)):
        sub = _safe_dir(sub, d, direct=True)
        if sub is None:
            continue
        if (sub / "package.json").is_file():
            c, t = _git_head(sub)
            repos[sub.name] = SnapRepo(sub.name, sub, c, t)
    return Snapshot(d, None, repos)
