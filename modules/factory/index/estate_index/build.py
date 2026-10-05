"""Build the estate index (one SQLite file) for one snapshot directory.

    snapshot/<repo>/...   (+ MANIFEST.json from `aa snapshot up`, optional)

Steps: walk files -> package.json map -> tree-sitter facts per code file -> resolve imports
(relative, @example-org/* -> sibling repo, builtin, external) -> resolve exports through
re-export chains -> resolve uses to symbols (local, import, this, static member, instance-by-
name) -> call-site counts, test links and per-symbol test hints, transitive IO -> repo edges
(declared vs measured; runtime vs test vs tooling) -> token counts -> SQLite (atomic replace).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import INDEX_VERSION, jsparse
from .resolve import INTERNAL_SCOPE, Package, Resolution, Resolver
from .tokens import Counter as TokCounter, default_counter

SKIP_DIRS = {"node_modules", ".git", "coverage", ".next", ".turbo", ".cache"}
MAX_PARSE_BYTES = 1_500_000
TEST_SUPPORT_DIRS = {"__tests__", "__mocks__", "__fixtures__", "test", "tests", "fixtures", "e2e", "test-utils",
                     "testutils"}
_TEST_RE = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
_CONFIG_RE = re.compile(r"^(jest|babel|eslint|prettier|webpack|rollup|vite|vitest|playwright|postcss|tailwind|"
                        r"next|nuxt|svelte|astro|commitlint|lint-staged|stylelint)([.\w-]*)?\.config\.[cm]?[jt]s$|"
                        r"^\.(eslintrc|prettierrc|babelrc|mocharc)\.[cm]?js$")
SCRIPT_DIRS = {"scripts", "tools", "bin"}
DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def file_role(path: str) -> str:
    parts = path.split("/")
    name, dirs = parts[-1], parts[:-1]
    if _TEST_RE.search(name):
        return "test"
    if any(d in TEST_SUPPORT_DIRS for d in dirs) or name in ("jest.setup.js", "setupTests.js", "setup-tests.js"):
        return "test_support"
    if _CONFIG_RE.match(name):
        return "config"
    if dirs and dirs[0] in SCRIPT_DIRS:
        return "script"
    return "src"


def edge_of(role: str) -> str:
    return {"test": "test", "test_support": "test", "config": "tooling", "script": "tooling"}.get(role, "runtime")


def _short(pkg: str | None, repo: str) -> str:
    if pkg and pkg.startswith(INTERNAL_SCOPE):
        return pkg[len(INTERNAL_SCOPE):]
    return repo


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


_isjunction = getattr(os.path, "isjunction", lambda _p: False)     # Windows junctions; Python >= 3.12


def _is_link(p: Path) -> bool:
    """Symlink or junction. The index never follows one: it could point outside the snapshot (aa's
    snapshot builder drops symlinks, so a link here did not come from `aa snapshot up`)."""
    return p.is_symlink() or _isjunction(p)


def snapshot_info(snapshot: Path) -> dict:
    """Snapshot id, MANIFEST digest and repo dirs (shared with the MCP server's freshness check)."""
    mpath = snapshot / "MANIFEST.json"
    manifest, msha = None, None
    if mpath.is_file() and not _is_link(mpath):
        raw = mpath.read_bytes()
        msha = hashlib.sha256(raw).hexdigest()
        try:
            manifest = json.loads(raw)
        except ValueError:
            manifest = None
    repos = sorted(d.name for d in snapshot.iterdir()
                   if d.is_dir() and not d.name.startswith(".") and not _is_link(d))
    sid = (manifest or {}).get("snapshot_id") or snapshot.name
    return {"snapshot_id": sid, "manifest": manifest, "manifest_sha256": msha, "repos": repos}


class _Builder:
    def __init__(self, snapshot: Path, counter: TokCounter):
        self.snapshot = snapshot
        self.counter = counter
        info = snapshot_info(snapshot)
        self.info = info
        self.manifest = info["manifest"] or {}
        self.repos: list[str] = info["repos"]
        self.mrepo = {r.get("name"): r for r in self.manifest.get("repos", []) if isinstance(r, dict)}
        self.files: list[dict] = []           # index = file id - 1
        self.fid: dict[tuple[str, str], int] = {}
        self.repo_files: dict[str, set[str]] = {r: set() for r in self.repos}
        self.packages: list[Package] = []
        self.timings: dict[str, float] = {}
        self.skipped_links = 0
        self._db: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ walk
    def walk(self) -> None:
        for repo in self.repos:
            root = self.snapshot / repo
            for dirpath, dirnames, filenames in os.walk(root):
                links = {d for d in dirnames if _is_link(Path(dirpath) / d)}
                self.skipped_links += len(links)
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and d not in links)
                rel_dir = Path(dirpath).relative_to(root).as_posix()
                rel_dir = "" if rel_dir == "." else rel_dir
                for fn in sorted(filenames):
                    rel = f"{rel_dir}/{fn}" if rel_dir else fn
                    p = Path(dirpath) / fn
                    if _is_link(p):
                        self.skipped_links += 1
                        continue
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    self.repo_files[repo].add(rel)
                    ext = posixpath.splitext(fn)[1].lower()
                    rec = {"id": len(self.files) + 1, "repo": repo, "path": rel, "abs": p, "bytes": size,
                           "ext": ext, "role": file_role(rel), "lang": jsparse.EXT_LANG.get(ext), "fx": None,
                           "src": None, "lines": None, "errors": 0}
                    self.files.append(rec)
                    self.fid[(repo, rel)] = rec["id"]
                    if fn == "package.json":
                        self._package(repo, rel_dir, p)

    def _package(self, repo: str, rel_dir: str, p: Path) -> None:
        pj = _read_json(p)
        if not isinstance(pj, dict) or not isinstance(pj.get("name"), str):
            return
        deps = {s: dict(pj.get(s) or {}) for s in DEP_SECTIONS if isinstance(pj.get(s), dict)}
        scripts = pj.get("scripts") if isinstance(pj.get("scripts"), dict) else {}
        self.packages.append(Package(name=pj["name"], repo=repo, dir=rel_dir, main=pj.get("main"),
                                     exports=pj.get("exports"), version=pj.get("version"),
                                     description=pj.get("description"), test_script=scripts.get("test"),
                                     deps=deps))

    # ------------------------------------------------------------------ parse
    def parse(self) -> None:
        for f in self.files:
            if f["lang"] is None:
                continue
            if f["bytes"] > MAX_PARSE_BYTES:
                f["errors"] = -1          # skipped: too large (minified bundle or data)
                continue
            src = f["abs"].read_bytes()
            fx = jsparse.parse_file(src, f["ext"])
            f["fx"], f["src"], f["lines"], f["errors"] = fx, src, fx.lines, fx.errors

    # ------------------------------------------------------------------ resolve + write
    def run(self, out: Path) -> dict:
        t0 = time.perf_counter()
        self.walk()
        t1 = time.perf_counter()
        self.parse()
        t2 = time.perf_counter()
        self.timings.update(walk_s=round(t1 - t0, 3), parse_s=round(t2 - t1, 3))

        pinned: dict[str, str] = {}
        for r in self.mrepo.values():
            for dep, pin in (r.get("pins") or {}).items():
                if isinstance(pin, dict) and pin.get("repo"):
                    pinned[dep] = pin["repo"]
        resolver = Resolver(self.repo_files, self.packages, pinned)
        pkg_at = {(p.repo, p.dir): p for p in self.packages}
        root_pkg = {p.repo: p for p in self.packages if p.dir == ""}

        def nearest_pkg(repo: str, path: str) -> Package | None:
            d = posixpath.dirname(path)
            while True:
                if (repo, d) in pkg_at:
                    return pkg_at[(repo, d)]
                if not d:
                    return None
                d = posixpath.dirname(d)

        # -- symbols: global ids
        syms: list[tuple[int, jsparse.Sym]] = []          # index = gid - 1
        base: dict[int, int] = {}
        top: dict[int, dict[str, int]] = defaultdict(dict)
        qual: dict[int, dict[str, int]] = defaultdict(dict)
        for f in self.files:
            fx = f["fx"]
            if fx is None:
                continue
            base[f["id"]] = len(syms) + 1
            for s in fx.symbols:
                gid = len(syms) + 1
                syms.append((f["id"], s))
                if s.parent is None:
                    top[f["id"]].setdefault(s.name, gid)
                qual[f["id"]].setdefault(s.qualname, gid)

        def gid_of(fid: int, local_idx: int | None) -> int | None:
            return None if local_idx is None else base[fid] + local_idx

        parent_gid = [gid_of(fid, s.parent) for fid, s in syms]
        children: dict[int, list[int]] = defaultdict(list)
        for g, p in enumerate(parent_gid, start=1):
            if p is not None:
                children[p].append(g)

        # -- imports
        imports: list[dict] = []
        binds: dict[int, dict[str, tuple]] = defaultdict(dict)
        for f in self.files:
            fx = f["fx"]
            if fx is None:
                continue
            npkg = nearest_pkg(f["repo"], f["path"])
            for imp in fx.imports:
                res: Resolution = resolver.resolve(f["repo"], f["path"], imp.spec)
                tfid = self.fid.get((res.repo, res.path)) if res.path else None
                section = None
                if res.package and npkg is not None:
                    section = next((s for s in DEP_SECTIONS if res.package in npkg.deps.get(s, {})), None)
                    if section is None and res.kind == "internal" and res.repo == f["repo"]:
                        section = "self"
                row = {"id": len(imports) + 1, "file_id": f["id"], "repo": f["repo"], "spec": imp.spec,
                       "kind": imp.kind, "line": imp.line, "local": imp.local, "member": imp.member,
                       "names": imp.names, "lazy": imp.lazy, "target_kind": res.kind, "target_repo": res.repo,
                       "target_file_id": tfid, "package": res.package, "dep_section": section,
                       "edge": edge_of(f["role"])}
                imports.append(row)
                if imp.local:
                    binds[f["id"]][imp.local] = (row["id"], tfid, imp.member, imp.member is None)
                for imported, loc in imp.names:
                    binds[f["id"]][loc] = (row["id"], tfid, imported, False)

        # -- exports (+ resolution through re-export chains)
        exp_named: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        exp_all: dict[int, list] = defaultdict(list)
        export_rows: list[dict] = []
        for f in self.files:
            fx = f["fx"]
            if fx is None:
                continue
            for e in fx.exports:
                tfid = None
                if e.spec:
                    res = resolver.resolve(f["repo"], f["path"], e.spec)
                    tfid = self.fid.get((res.repo, res.path)) if res.path else None
                row = {"file_id": f["id"], "name": e.name, "kind": e.kind, "local": e.local,
                       "sym": gid_of(f["id"], e.sym), "spec": e.spec, "imported": e.imported, "line": e.line,
                       "tfid": tfid}
                export_rows.append(row)
                if e.kind in ("reexport_all",):
                    exp_all[f["id"]].append(row)
                elif e.kind == "spread_local":
                    exp_all[f["id"]].append(row)
                else:
                    exp_named[f["id"]][e.name].append(row)

        memo: dict[tuple[int, str], int | None] = {}

        def resolve_export(fid: int | None, name: str | None, seen: set | None = None) -> int | None:
            if fid is None or not name:
                return None
            key = (fid, name)
            if key in memo:
                return memo[key]
            seen = seen if seen is not None else set()
            if key in seen or len(seen) > 40:
                return None
            seen.add(key)
            res = None
            for e in exp_named[fid].get(name, []):
                k = e["kind"]
                if k == "inline":
                    res = e["sym"]
                elif k in ("local", "instance"):
                    loc = e["local"]
                    if loc in top[fid]:
                        res = top[fid][loc]
                    elif loc in binds[fid]:
                        _, tfid, imported, is_ns = binds[fid][loc]
                        if imported:
                            res = resolve_export(tfid, imported, seen)
                        elif k == "instance" or name == "default":
                            res = resolve_export(tfid, "default", seen)
                elif k == "reexport":
                    res = resolve_export(e["tfid"], e["imported"], seen)
                if res is not None:
                    break
            if res is None and name != "default":
                for e in exp_all[fid]:
                    if e["kind"] == "reexport_all":
                        res = resolve_export(e["tfid"], name, seen)
                    elif e["local"] in binds[fid]:
                        _, tfid, imported, is_ns = binds[fid][e["local"]]
                        if is_ns:
                            res = resolve_export(tfid, name, seen)
                    if res is not None:
                        break
            memo[key] = res
            return res

        for row in export_rows:
            if row["sym"] is None and row["kind"] in ("local", "instance", "reexport"):
                row["sym"] = resolve_export(row["file_id"], row["name"])

        exported: dict[int, set[str]] = defaultdict(set)
        for row in export_rows:
            if row["sym"] is not None and row["kind"] != "module":
                exported[row["sym"]].add(row["name"])
        for g in list(exported):
            fid, s = syms[g - 1]
            if s.kind in ("class", "object"):
                for c in children.get(g, []):
                    cs = syms[c - 1][1]
                    if not cs.private:
                        exported[c].add(f"{s.name}.{cs.name}")

        # -- uses -> refs
        def member_of(g: int | None, member: str) -> int | None:
            if g is None:
                return None
            fid, s = syms[g - 1]
            if s.kind not in ("class", "object"):
                return None
            return qual[fid].get(f"{s.qualname}.{member}")

        def class_of(g: int | None) -> int | None:
            while g is not None:
                s = syms[g - 1][1]
                if s.kind in ("class", "object"):
                    return g
                g = parent_gid[g - 1]
            return None

        role_of = {f["id"]: f["role"] for f in self.files}
        repo_of_file = {f["id"]: f["repo"] for f in self.files}
        refs: list[tuple] = []        # (file_id, caller, callee, kind, via, line, n, import_id)
        this_calls: dict[int, Counter] = defaultdict(Counter)
        for f in self.files:
            fx = f["fx"]
            if fx is None:
                continue
            fid = f["id"]
            fb = binds[fid]
            ft = top[fid]
            for u, owner in zip(fx.uses, fx.use_owner):
                caller = gid_of(fid, owner)
                callee, via, imp_id = None, None, None
                if u.member is None:
                    b = fb.get(u.name)
                    if b is None and u.name in ft:
                        callee, via = ft[u.name], "local"
                    elif b is not None:
                        imp_id, tfid, imported, is_ns = b
                        if imported:
                            callee = resolve_export(tfid, imported)
                        elif u.kind in ("call", "new"):
                            callee = resolve_export(tfid, "default")
                        via = "import"
                elif u.name == "this":
                    cls = class_of(caller)
                    if cls is not None:
                        callee, via = member_of(cls, u.member), "this"
                        if u.kind == "call":
                            this_calls[fid][u.member] += 1
                else:
                    b = fb.get(u.name)
                    if b is None and u.name in ft:
                        callee, via = member_of(ft[u.name], u.member), "member"
                    elif b is not None:
                        imp_id, tfid, imported, is_ns = b
                        via = "import"
                        if is_ns:
                            callee = resolve_export(tfid, u.member)
                            if callee is None:
                                callee = member_of(resolve_export(tfid, "default"), u.member)
                        else:
                            callee = member_of(resolve_export(tfid, imported), u.member)
                if callee is None or callee == caller:
                    continue
                refs.append((fid, caller, callee, u.kind, via, u.line, 1, imp_id))

        # instance calls: `.m(` in a file that constructs / imports class C counts toward C.m. Calls on an
        # import binding (`pool.query(` with pool from require('./db'), `path.join(`) are not: the import path
        # above already resolved them, or they belong to another module. Builtins (console.log) are never
        # counted (jsparse.BUILTIN_RECEIVERS).
        classes_in: dict[int, set[int]] = defaultdict(set)
        for r in refs:
            if syms[r[2] - 1][1].kind == "class" and (r[3] == "new" or r[4] != "local"):
                classes_in[r[0]].add(r[2])
        for f in self.files:
            fx = f["fx"]
            if fx is None or not fx.member_calls:
                continue
            fid = f["id"]
            classes = classes_in.get(fid, ())
            on_imports: Counter = Counter()
            for (mname, recv), k in fx.member_recv.items():
                if recv in binds[fid]:
                    on_imports[mname] += k
            for c in classes:
                for m in children.get(c, []):
                    ms = syms[m - 1][1]
                    if ms.kind != "method" or ms.is_static:
                        continue
                    n = fx.member_calls.get(ms.name, 0) - this_calls[fid].get(ms.name, 0) - on_imports[ms.name]
                    if n > 0:
                        refs.append((fid, None, m, "call", "instance", None, n, None))

        # -- stats, test links, symbol tests
        stats: dict[int, dict] = defaultdict(lambda: {"same_file": 0, "same_repo": 0, "cross": 0,
                                                       "files": set(), "repos": set(), "refs": 0,
                                                       "test_refs": 0, "test_files": set()})
        sym_tests: dict[tuple[int, int, str], int] = Counter()
        for fid, _caller, callee, kind, via, _line, n, _imp in refs:
            cfid = syms[callee - 1][0]
            st = stats[callee]
            role = role_of[fid]
            if role in ("test", "test_support"):
                st["test_refs"] += n
                if role == "test":
                    st["test_files"].add(fid)
                    sym_tests[(callee, fid, "instance" if via == "instance" else kind)] += n
                continue
            if kind in ("call", "new"):
                if fid == cfid:
                    st["same_file"] += n
                elif repo_of_file[fid] == repo_of_file[cfid]:
                    st["same_repo"] += n
                else:
                    st["cross"] += n
            else:
                st["refs"] += n
            if fid != cfid:
                st["files"].add(fid)
                st["repos"].add(repo_of_file[fid])

        test_links: set[tuple[int, int, str]] = set()
        for row in imports:
            if role_of[row["file_id"]] == "test" and row["target_file_id"]:
                test_links.add((row["file_id"], row["target_file_id"],
                                "jest_mock" if row["kind"] == "jest_mock" else "import"))
        hop: dict[int, set[int]] = defaultdict(set)     # re-export edges, one hop
        for row in export_rows:
            if row["tfid"]:
                hop[row["file_id"]].add(row["tfid"])
            elif row["kind"] in ("local", "spread_local") and row["local"] in binds[row["file_id"]]:
                tf = binds[row["file_id"]][row["local"]][1]   # const { x } = require('./x'); module.exports = { x }
                if tf:
                    hop[row["file_id"]].add(tf)
        for t, target, _via in list(test_links):
            for nxt in hop.get(target, ()):
                test_links.add((t, nxt, "reexport"))
        linked = {(t, target) for t, target, _ in test_links}
        for f in self.files:
            if f["role"] != "test":
                continue
            d = posixpath.dirname(f["path"])
            stem = _TEST_RE.sub("", posixpath.basename(f["path"]))
            dirs = [d]
            if posixpath.basename(d) in ("__tests__", "test", "tests"):
                dirs.append(posixpath.dirname(d))
            for dd in dirs:
                for cand in (posixpath.join(dd, stem + ".js"), posixpath.join(dd, stem + ".ts"),
                             posixpath.join(dd, stem, "index.js")):
                    tf = self.fid.get((f["repo"], cand.lstrip("/")))
                    if tf and (f["id"], tf) not in linked and role_of.get(tf) == "src":
                        test_links.add((f["id"], tf, "name_match"))
                        linked.add((f["id"], tf))
        module_tested = {target for _, target, _ in test_links}

        name_calls: Counter = Counter()
        for f in self.files:
            if f["fx"] is not None and f["role"] not in ("test", "test_support"):
                name_calls.update(f["fx"].member_calls)

        # transitive IO over same-repo runtime calls (depth 3)
        adj: dict[int, set[int]] = defaultdict(set)
        for fid, caller, callee, kind, _via, _line, _n, _imp in refs:
            if caller is not None and kind in ("call", "new") and role_of[fid] not in ("test", "test_support") \
                    and repo_of_file[fid] == repo_of_file[syms[callee - 1][0]]:
                adj[caller].add(callee)

        def io_closure(g: int) -> set[str]:
            out = set(syms[g - 1][1].io)
            frontier, seen = [g], {g}
            for _ in range(3):
                nxt = []
                for x in frontier:
                    for y in adj.get(x, ()):
                        if y not in seen:
                            seen.add(y)
                            out |= syms[y - 1][1].io
                            nxt.append(y)
                frontier = nxt
            return out

        # -- tokens
        t3 = time.perf_counter()
        sym_texts, sig_texts = [], []
        for fid, s in syms:
            src = self.files[fid - 1]["src"]
            sym_texts.append(src[s.start:s.end].decode("utf-8", "replace"))
            doc = src[s.jsdoc["start"]:s.jsdoc["end"]].decode("utf-8", "replace") + "\n" if s.jsdoc else ""
            sig_texts.append(doc + s.signature)
        sym_tok = self.counter.count_many(sym_texts)
        sig_tok = self.counter.count_many(sig_texts)
        code_files = [f for f in self.files if f["src"] is not None]
        file_tok = self.counter.count_many([f["src"].decode("utf-8", "replace") for f in code_files])
        for f, n in zip(code_files, file_tok):
            f["tokens"] = n
        self.timings["tokens_s"] = round(time.perf_counter() - t3, 3)

        # -- write
        t4 = time.perf_counter()
        tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
        if tmp.exists():
            tmp.unlink()
        db = sqlite3.connect(tmp)
        self._db = db
        db.executescript((Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8"))
        repo_id = {r: i for i, r in enumerate(self.repos, start=1)}
        cnt_files = Counter(f["repo"] for f in self.files)
        cnt_code = Counter(f["repo"] for f in self.files if f["fx"] is not None)
        cnt_test = Counter(f["repo"] for f in self.files if f["role"] == "test")
        lines = Counter()
        btot = Counter()
        ttot = Counter()
        for f in self.files:
            btot[f["repo"]] += f["bytes"]
            if f["fx"] is not None:
                lines[f["repo"]] += f["lines"] or 0
                ttot[f["repo"]] += f.get("tokens", 0)
        for r in self.repos:
            p = root_pkg.get(r)
            m = self.mrepo.get(r, {})
            has_jest = int(bool(p and any("jest" in p.deps.get(s, {}) for s in DEP_SECTIONS)))
            db.execute("INSERT INTO repos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (repo_id[r], r, _short(p.name if p else None, r), p.name if p else None,
                        p.version if p else None, p.description if p else None, m.get("commit"), m.get("branch"),
                        int(bool(m.get("dirty"))), cnt_files[r], cnt_code[r], cnt_test[r], lines[r], btot[r],
                        ttot[r], has_jest, p.test_script if p else None))
        for p in self.packages:
            db.execute("INSERT INTO packages (repo_id, name, dir, main, version) VALUES (?,?,?,?,?)",
                       (repo_id[p.repo], p.name, p.dir, p.main, p.version))
            pins = (self.mrepo.get(p.repo, {}).get("pins") or {}) if p.dir == "" else {}
            for section, deps in p.deps.items():
                for dep, spec in deps.items():
                    internal = dep.startswith(INTERNAL_SCOPE) or dep in resolver.pkg_by_name
                    tgt = resolver.pkg_by_name[dep].repo if dep in resolver.pkg_by_name else pinned.get(dep)
                    pin = pins.get(dep) or {}
                    drift = pin.get("drift") or {}
                    db.execute("INSERT INTO pkg_deps VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (repo_id[p.repo], p.dir, dep, section, str(spec)[:200], int(internal), tgt,
                                pin.get("ref"), drift.get("commits_past_pin"), drift.get("files_changed")))
        db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?)",
                       [(f["id"], repo_id[f["repo"]], f["path"], f["lang"], f["role"], f["bytes"], f["lines"],
                         f["errors"], f.get("tokens"), len(f["fx"].symbols) if f["fx"] else None)
                        for f in self.files])

        def targetable(g: int) -> bool:
            s = syms[g - 1][1]
            if s.inline_export:
                return False
            p = parent_gid[g - 1]
            return p is None or syms[p - 1][1].kind == "class"

        sym_rows = []
        for g, (fid, s) in enumerate(syms, start=1):
            jd = s.jsdoc
            complete = 0
            if jd:
                if s.kind in ("function", "method", "constructor", "setter"):
                    named = [p for p in jd["params"] if p.get("name")]
                    complete = int(len(named) >= len(s.params) and all(p.get("type") for p in named)
                                   and (not s.returns_value or bool(jd["returns"]) or s.kind == "constructor"))
                else:
                    complete = 1
            jd_json = json.dumps({k: jd[k] for k in ("params", "returns", "type", "summary")},
                                 separators=(",", ":")) if jd else None
            io_t = sorted(io_closure(g)) if s.kind in ("function", "method", "constructor", "getter", "setter") \
                else sorted(s.io)
            sym_rows.append((g, fid, repo_id[self.files[fid - 1]["repo"]], parent_gid[g - 1], s.name, s.qualname,
                             s.kind, s.start, s.end, s.line, s.end_line, s.signature,
                             json.dumps(s.params, separators=(",", ":")), int(s.is_async), int(s.is_static),
                             int(s.is_generator), int(s.private), int(g in exported),
                             ",".join(sorted(exported.get(g, ()))) or None, int(jd is not None), jd_json, complete,
                             s.branchiness, int(s.returns_value), ",".join(sorted(s.io)) or None,
                             ",".join(io_t) or None, s.end_line - s.line + 1, sym_tok[g - 1], sig_tok[g - 1],
                             int(s.inline_export), int(targetable(g))))
        db.executemany(f"INSERT INTO symbols VALUES ({','.join('?' * 31)})", sym_rows)
        def reexport_target(r: dict) -> int | None:
            if r["tfid"]:
                return r["tfid"]
            b = binds[r["file_id"]].get(r["local"]) if r["local"] else None
            return b[1] if b is not None and r["kind"] in ("local", "spread_local", "instance") else None

        db.executemany("INSERT INTO exports (file_id, name, kind, local, symbol_id, spec, imported, line, "
                       "target_file_id) VALUES (?,?,?,?,?,?,?,?,?)",
                       [(r["file_id"], r["name"], r["kind"], r["local"], r["sym"], r["spec"], r["imported"],
                         r["line"], reexport_target(r)) for r in export_rows])
        db.executemany("INSERT INTO imports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       [(r["id"], r["file_id"], repo_id[r["repo"]], r["spec"], r["kind"], r["line"], r["local"],
                         r["member"], json.dumps(r["names"], separators=(",", ":")) if r["names"] else None,
                         int(r["lazy"]), r["target_kind"], r["target_repo"], r["target_file_id"], r["package"],
                         r["dep_section"], r["edge"]) for r in imports])
        name_rows = []
        for r in imports:
            for imported, loc in r["names"]:
                name_rows.append((r["id"], imported, loc, resolve_export(r["target_file_id"], imported)))
            if r["member"]:
                name_rows.append((r["id"], r["member"], r["local"], resolve_export(r["target_file_id"], r["member"])))
        db.executemany("INSERT INTO import_names VALUES (?,?,?,?)", name_rows)
        db.executemany("INSERT INTO refs (file_id, caller_symbol_id, callee_symbol_id, kind, via, line, n, import_id,"
                       " cross_repo, from_test) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       [(fid, caller, callee, kind, via, line, n, imp_id,
                         int(repo_of_file[fid] != repo_of_file[syms[callee - 1][0]]),
                         int(role_of[fid] in ("test", "test_support")))
                        for fid, caller, callee, kind, via, line, n, imp_id in refs])
        db.executemany("INSERT INTO member_calls VALUES (?,?,?)",
                       [(f["id"], k, v) for f in self.files if f["fx"] is not None
                        for k, v in f["fx"].member_calls.items()])
        db.executemany("INSERT INTO test_links VALUES (?,?,?)", sorted(test_links))
        db.executemany("INSERT INTO symbol_tests VALUES (?,?,?,?)",
                       [(s, t, ev, n) for (s, t, ev), n in sorted(sym_tests.items())])
        newed_in_test = {callee for (callee, _t, ev) in sym_tests if ev == "new"}   # `new C()` in a test runs C's ctor
        stat_rows = []
        for g, (fid, s) in enumerate(syms, start=1):
            st = stats.get(g)
            direct = bool(st and st["test_files"])
            p = parent_gid[g - 1]
            if not direct and s.kind == "constructor" and p in newed_in_test:
                level = "direct"
            elif not direct and s.kind in ("method", "getter", "setter", "constructor"):
                level = "module" if (p in stats and stats[p]["test_files"]) or fid in module_tested else "none"
            else:
                level = "direct" if direct else ("module" if fid in module_tested else "none")
            stat_rows.append((g, st["same_file"] if st else 0, st["same_repo"] if st else 0, st["cross"] if st else 0,
                              len(st["files"]) if st else 0, len(st["repos"]) if st else 0,
                              st["refs"] if st else 0, st["test_refs"] if st else 0,
                              len(st["test_files"]) if st else 0, level,
                              name_calls.get(s.name, 0) if s.kind in ("method", "getter") else None))
        db.executemany("INSERT INTO symbol_stats VALUES (?,?,?,?,?,?,?,?,?,?,?)", stat_rows)

        # repo edges: declared (root package.json) + measured imports
        declared: dict[tuple[str, str], str] = {}
        for p in self.packages:
            if p.dir != "":
                continue
            for section, deps in p.deps.items():
                for dep in deps:
                    tgt = resolver.pkg_by_name[dep].repo if dep in resolver.pkg_by_name else pinned.get(dep)
                    if tgt and tgt != p.repo and (dep.startswith(INTERNAL_SCOPE) or dep in resolver.pkg_by_name):
                        prev = declared.get((p.repo, tgt))
                        if prev is None or section == "dependencies":
                            declared[(p.repo, tgt)] = section
        measured: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for r in imports:
            if r["target_repo"] and r["target_repo"] != r["repo"] and r["target_kind"] in ("internal",
                                                                                          "missing_repo"):
                measured[(r["repo"], r["target_repo"])][r["edge"]] += 1
        for key in sorted(set(declared) | set(measured)):
            m = measured.get(key, Counter())
            kind = "runtime" if m["runtime"] else ("test" if m["test"] else ("tooling" if m["tooling"] else
                                                                            "declared_only"))
            db.execute("INSERT INTO repo_edges VALUES (?,?,?,?,?,?,?,?)",
                       (key[0], key[1], declared.get(key), m["runtime"], m["test"], m["tooling"],
                        int(key[1] in repo_id), kind))

        for stmt in (
            "CREATE INDEX ix_files_repo ON files(repo_id, path)",
            "CREATE INDEX ix_sym_file ON symbols(file_id)",
            "CREATE INDEX ix_sym_name ON symbols(name)",
            "CREATE INDEX ix_sym_qual ON symbols(qualname)",
            "CREATE INDEX ix_exp_file ON exports(file_id, name)",
            "CREATE INDEX ix_exp_sym ON exports(symbol_id)",
            "CREATE INDEX ix_exp_target ON exports(target_file_id)",
            "CREATE INDEX ix_imp_file ON imports(file_id)",
            "CREATE INDEX ix_imp_target ON imports(target_file_id)",
            "CREATE INDEX ix_imp_trepo ON imports(target_repo)",
            "CREATE INDEX ix_in_imp ON import_names(import_id)",
            "CREATE INDEX ix_in_sym ON import_names(symbol_id)",
            "CREATE INDEX ix_refs_callee ON refs(callee_symbol_id)",
            "CREATE INDEX ix_refs_caller ON refs(caller_symbol_id)",
            "CREATE INDEX ix_refs_file ON refs(file_id)",
            "CREATE INDEX ix_tl_target ON test_links(target_file_id)",
            "CREATE INDEX ix_tl_test ON test_links(test_file_id)",
            "CREATE INDEX ix_st_sym ON symbol_tests(symbol_id)",
        ):
            db.execute(stmt)

        counts = {
            "repos": len(self.repos), "files": len(self.files), "code_files": len(code_files),
            "test_files": sum(1 for f in self.files if f["role"] == "test"), "symbols": len(syms),
            "symbols_by_kind": dict(sorted(Counter(s.kind for _, s in syms).items())),
            "exported_symbols": sum(1 for g in range(1, len(syms) + 1) if g in exported),
            "imports": len(imports),
            "imports_by_target": dict(sorted(Counter(r["target_kind"] for r in imports).items())),
            "cross_repo_imports": sum(1 for r in imports if r["target_repo"] and r["target_repo"] != r["repo"]),
            "refs": len(refs), "test_links": len(test_links),
            "parse_errors": sum(f["errors"] for f in self.files if f["errors"] > 0),
            "skipped_large": sum(1 for f in self.files if f["errors"] == -1),
            "skipped_links": self.skipped_links,
        }
        self.timings["write_s"] = round(time.perf_counter() - t4, 3)
        from .jsparse import parser_versions
        meta = {
            "index_version": INDEX_VERSION,
            "snapshot_id": self.info["snapshot_id"],
            "snapshot_dir": str(self.snapshot),
            "manifest_sha256": self.info["manifest_sha256"],
            "built_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "counted_by": self.counter.counted_by,
            "tokenizer_exact": int(self.counter.exact),
            "parser_versions": parser_versions(),
            "repos": self.repos,
            "counts": counts,
        }
        meta["build_seconds"] = round(time.perf_counter() - t0, 3)
        meta["timings"] = self.timings
        db.executemany("INSERT INTO meta VALUES (?,?)",
                       [(k, v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))) for k, v in meta.items()])
        db.commit()
        db.execute("PRAGMA journal_mode=DELETE")
        db.close()
        self._db = None
        os.replace(tmp, out)
        return meta


def build_index(snapshot: str | Path, out: str | Path, counter: TokCounter | None = None) -> dict:
    """Index `snapshot` into the SQLite file `out` (written atomically). Returns the meta dict."""
    snapshot = Path(snapshot)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    builder = _Builder(snapshot, counter or default_counter())
    tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
    try:
        return builder.run(out)
    except BaseException:
        if builder._db is not None:
            builder._db.close()
            builder._db = None
        tmp.unlink(missing_ok=True)
        raise


def open_ro(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def read_meta(conn: sqlite3.Connection) -> dict:
    out = {}
    for k, v in conn.execute("SELECT key, value FROM meta"):
        try:
            out[k] = json.loads(v) if v and v[:1] in "[{" or k in ("build_seconds", "tokenizer_exact") else v
        except ValueError:
            out[k] = v
    return out
