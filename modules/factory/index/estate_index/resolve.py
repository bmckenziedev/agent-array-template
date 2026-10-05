"""Static module resolution across the repos of one snapshot (no npm install, no node).

`@example-org/<pkg>[/sub/path]` maps to the sibling repo whose package.json has that name
(nested packages such as nested packages included), then Node's file
resolution runs inside that repo: exact, + extension, /index.*, package.json "main", and a
minimal "exports" map. A scoped package that is pinned in a MANIFEST but not in the
snapshot resolves to kind `missing_repo` with the repo name from the pin.
"""
from __future__ import annotations

import posixpath
import os
from dataclasses import dataclass, field

NODE_BUILTINS = {
    "assert", "assert/strict", "async_hooks", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "diagnostics_channel", "dns", "dns/promises", "domain", "events", "fs", "fs/promises",
    "http", "http2", "https", "inspector", "module", "net", "os", "path", "path/posix", "path/win32",
    "perf_hooks", "process", "punycode", "querystring", "readline", "readline/promises", "repl", "stream",
    "stream/promises", "stream/web", "string_decoder", "sys", "test", "timers", "timers/promises", "tls",
    "trace_events", "tty", "url", "util", "util/types", "v8", "vm", "wasi", "worker_threads", "zlib",
}
EXTS = ("", ".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".json", ".node")
INDEX_FILES = ("index.js", "index.cjs", "index.mjs", "index.jsx", "index.ts", "index.tsx", "index.json")
INTERNAL_SCOPE = os.environ.get("FACTORY_PACKAGE_SCOPE", "@example-org/")


@dataclass
class Package:
    name: str
    repo: str
    dir: str                 # repo-relative directory ('' for the repo root)
    main: str | None = None
    exports: object = None
    version: str | None = None
    description: str | None = None
    test_script: str | None = None
    deps: dict[str, dict[str, str]] = field(default_factory=dict)   # section -> {name: spec}


@dataclass
class Resolution:
    kind: str                # relative|internal|external|builtin|missing_repo|unresolved
    repo: str | None = None
    path: str | None = None
    package: str | None = None


def package_name(spec: str) -> tuple[str, str]:
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2]), "/".join(parts[2:])
    return parts[0], "/".join(parts[1:])


class Resolver:
    def __init__(self, repo_files: dict[str, set[str]], packages: list[Package],
                 pinned_repos: dict[str, str] | None = None):
        self.repo_files = repo_files
        self.pkg_by_name: dict[str, Package] = {}
        for p in sorted(packages, key=lambda p: (p.dir.count("/"), p.dir)):   # repo root wins a name clash
            self.pkg_by_name.setdefault(p.name, p)
        self.pinned = dict(pinned_repos or {})
        self._cache: dict[tuple[str, str, str], Resolution] = {}

    def _try(self, repo: str, base: str) -> str | None:
        files = self.repo_files.get(repo, set())
        base = base.strip("/")
        for ext in EXTS:
            if base + ext in files:
                return base + ext
        for idx in INDEX_FILES:
            cand = posixpath.join(base, idx) if base else idx
            if cand in files:
                return cand
        pj = posixpath.join(base, "package.json") if base else "package.json"
        if pj in files:
            pkg = next((p for p in self.pkg_by_name.values() if p.repo == repo and p.dir == base), None)
            if pkg is not None and pkg.main:
                return self._try_file_only(repo, posixpath.normpath(posixpath.join(base, pkg.main)))
        return None

    def _try_file_only(self, repo: str, base: str) -> str | None:
        files = self.repo_files.get(repo, set())
        for ext in EXTS:
            if base + ext in files:
                return base + ext
        for idx in INDEX_FILES:
            if posixpath.join(base, idx) in files:
                return posixpath.join(base, idx)
        return None

    def _package_entry(self, pkg: Package, sub: str) -> str | None:
        exp = pkg.exports
        key = "./" + sub if sub else "."
        target = None
        if isinstance(exp, str) and not sub:
            target = exp
        elif isinstance(exp, dict):
            v = exp.get(key)
            if v is None and not sub and not any(k.startswith(".") for k in exp):
                v = exp           # conditions object for "."
            if isinstance(v, dict):
                v = v.get("require") or v.get("node") or v.get("default")
            if isinstance(v, str):
                target = v
        if target:
            hit = self._try_file_only(pkg.repo, posixpath.normpath(posixpath.join(pkg.dir, target)))
            if hit:
                return hit
        if sub:
            return self._try(pkg.repo, posixpath.normpath(posixpath.join(pkg.dir, sub)) if pkg.dir else sub)
        if pkg.main:
            hit = self._try_file_only(pkg.repo, posixpath.normpath(posixpath.join(pkg.dir, pkg.main)))
            if hit:
                return hit
        return self._try(pkg.repo, pkg.dir)

    def resolve(self, repo: str, from_path: str, spec: str) -> Resolution:
        key = (repo, posixpath.dirname(from_path), spec)
        if key in self._cache:
            return self._cache[key]
        res = self._resolve(repo, from_path, spec)
        self._cache[key] = res
        return res

    def _resolve(self, repo: str, from_path: str, spec: str) -> Resolution:
        if spec.startswith((".", "/")):
            if spec.startswith("/"):
                return Resolution("unresolved")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
            if target.startswith(".."):
                return Resolution("unresolved")
            hit = self._try(repo, "" if target == "." else target)
            return Resolution("relative", repo, hit) if hit else Resolution("unresolved", repo)
        bare = spec[5:] if spec.startswith("node:") else spec
        if spec.startswith("node:") or bare in NODE_BUILTINS:
            return Resolution("builtin", package=bare)
        name, sub = package_name(bare)
        pkg = self.pkg_by_name.get(name)
        if pkg is not None:
            hit = self._package_entry(pkg, sub)
            return Resolution("internal", pkg.repo, hit, name) if hit else Resolution("unresolved", pkg.repo,
                                                                                         package=name)
        if name in self.pinned or name.startswith(INTERNAL_SCOPE):
            return Resolution("missing_repo", self.pinned.get(name), None, name)
        return Resolution("external", package=name)
