"""Read-only queries over an estate index (shared by the CLI and the MCP server).

Every function takes an open sqlite3 connection (row_factory = sqlite3.Row) and returns
plain JSON-able dicts, already paginated / capped so an MCP response stays well under
~8k tokens.
"""
from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from collections import Counter, defaultdict

from . import graph

# Targets the secret-free, network-free tester cannot run without a profile (bench IO_SKIP_DEFAULT).
IO_SKIP = {"module:child_process", "module:fs", "module:fs/promises", "module:net", "module:http", "module:https",
           "module:http2", "module:tls", "module:dgram", "module:ws", "module:pg", "module:cluster",
           "module:worker_threads", "process.exit", "setInterval", "fetch"}
FUNC_KINDS = ("function", "method")


_FILE_RE = re.compile(r"\.(c|m)?[jt]sx?$|\.json$")


class TargetError(ValueError):
    pass


def _rows(conn: sqlite3.Connection, sql: str, *args) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def _like_prefix(prefix: str) -> str:
    """`path LIKE ? ESCAPE '\\'` pattern for a literal path prefix (`_` and `%` in paths are not wildcards)."""
    p = prefix.strip("/")
    return p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _page(items: list, offset: int, limit: int) -> dict:
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 20), 200))
    page = items[offset:offset + limit]
    nxt = offset + limit if offset + limit < len(items) else None
    return {"total": len(items), "offset": offset, "limit": limit, "next_offset": nxt, "items": page}


# --------------------------------------------------------------------------- #
# Target resolution
# --------------------------------------------------------------------------- #
def repos(conn) -> dict[str, sqlite3.Row]:
    return {r["name"]: r for r in _rows(conn, "SELECT * FROM repos")}


def _repo_by_alias(conn, name: str) -> sqlite3.Row | None:
    for r in _rows(conn, "SELECT * FROM repos"):
        if name in (r["name"], r["short"], r["package"]):
            return r
    return None


def _file_by_path(conn, repo_id: int, path: str) -> sqlite3.Row | None:
    path = path.strip("/")
    for cand in (path, path + ".js", path + ".cjs", path + ".mjs", path + ".ts", path + ".tsx", path + ".json",
                 posixpath.join(path, "index.js"), posixpath.join(path, "index.ts")):
        r = conn.execute("SELECT * FROM files WHERE repo_id=? AND path=?", (repo_id, cand)).fetchone()
        if r is not None:
            return r
    return None


def _split_symbol(target: str) -> tuple[str, str | None]:
    for sep in ("#", "::"):
        if sep in target:
            a, b = target.split(sep, 1)
            return a, b or None
    return target, None


def resolve_target(conn, target: str, repo: str | None = None, symbol: str | None = None) -> dict:
    """repo | short name | @scope/pkg | @scope/pkg/sub/path | repo/path/file.js | path (with repo) | symbol,
    each optionally suffixed with #Symbol (or ::Symbol)."""
    if not target or not str(target).strip():
        raise TargetError("empty target")
    t, sym = _split_symbol(str(target).strip())
    sym = symbol or sym
    out: dict = {"input": target}
    rid = None
    r = _repo_by_alias(conn, t)
    if r is not None and not sym:
        return {**out, "type": "repo", "repo": r["name"], "package": r["package"]}
    file = None
    if r is None and t.startswith("@"):
        parts = t.split("/")
        pkg, sub = "/".join(parts[:2]), "/".join(parts[2:])
        prow = conn.execute("SELECT p.*, r.name AS repo FROM packages p JOIN repos r ON r.id=p.repo_id "
                            "WHERE p.name=? ORDER BY length(p.dir) LIMIT 1", (pkg,)).fetchone()
        if prow is None:
            raise TargetError(f"package {pkg} is not in this snapshot")
        if not sub and not sym:
            return {**out, "type": "repo", "repo": prow["repo"], "package": pkg}
        rid = conn.execute("SELECT id FROM repos WHERE name=?", (prow["repo"],)).fetchone()["id"]
        if sub:
            file = _file_by_path(conn, rid, posixpath.join(prow["dir"], sub) if prow["dir"] else sub)
        else:
            main = prow["main"] or "index.js"
            file = _file_by_path(conn, rid, posixpath.normpath(posixpath.join(prow["dir"], main)))
        if file is None:
            raise TargetError(f"{t}: no such module in {prow['repo']}")
    elif r is not None and sym:
        rid = r["id"]
    else:
        parts = t.replace("\\", "/").split("/")
        rr = _repo_by_alias(conn, parts[0]) if len(parts) > 1 else None
        if rr is None and repo:
            rr = _repo_by_alias(conn, repo)
            if rr is None:
                raise TargetError(f"unknown repo {repo}")
            path = t
        elif rr is not None:
            path = "/".join(parts[1:])
        else:
            path = None
        if rr is not None and path:
            file = _file_by_path(conn, rr["id"], path)
            if file is None:
                raise TargetError(f"{rr['name']}/{path}: no such file in the snapshot")
        elif "/" not in t and not _FILE_RE.search(t):
            sym = sym or t          # a bare symbol name (Class.method allowed)
            rr = _repo_by_alias(conn, repo) if repo else None
            rid = rr["id"] if rr is not None else None
        else:
            raise TargetError(f"cannot resolve {target!r}: give repo/path, @example-org/pkg/path or a repo name")
    if file is not None:
        frow = conn.execute("SELECT f.*, r.name AS repo FROM files f JOIN repos r ON r.id=f.repo_id WHERE f.id=?",
                            (file["id"],)).fetchone()
        out.update(type="file", repo=frow["repo"], file_id=frow["id"], path=frow["path"], role=frow["role"])
        if not sym:
            return out
        srow = conn.execute("SELECT * FROM symbols WHERE file_id=? AND (qualname=? OR name=?) "
                            "ORDER BY (qualname=?) DESC, parent_id IS NOT NULL LIMIT 1",
                            (frow["id"], sym, sym, sym)).fetchone()
        if srow is None:
            ex = conn.execute("SELECT symbol_id FROM exports WHERE file_id=? AND name=? AND symbol_id IS NOT NULL",
                              (frow["id"], sym)).fetchone()
            if ex is not None:
                srow = conn.execute("SELECT * FROM symbols WHERE id=?", (ex["symbol_id"],)).fetchone()
        if srow is None:
            raise TargetError(f"{frow['repo']}/{frow['path']}: no symbol {sym!r}")
        return _symbol_target(conn, srow, out)
    # bare symbol search
    args: list = [sym, sym]
    where = "(s.qualname=? OR s.name=?)"
    if rid is not None:
        where += " AND s.repo_id=?"
        args.append(rid)
    cands = _rows(conn, f"SELECT s.*, f.path, r.name AS repo FROM symbols s JOIN files f ON f.id=s.file_id "
                        f"JOIN repos r ON r.id=s.repo_id WHERE {where} AND f.role != 'test' "
                        f"ORDER BY s.exported DESC, s.qualname=? DESC LIMIT 25", *args, sym)
    if not cands:
        raise TargetError(f"no symbol named {sym!r}")
    exported = [c for c in cands if c["exported"]]
    pick = exported if exported else cands
    if len(pick) > 1 and len({(c["repo"], c["path"]) for c in pick}) > 1:
        raise TargetError(json.dumps({"ambiguous": sym, "candidates": [
            f"{c['repo']}/{c['path']}#{c['qualname']}" for c in pick[:12]]}))
    return _symbol_target(conn, pick[0], out)


def _symbol_target(conn, srow, out: dict) -> dict:
    frow = conn.execute("SELECT f.*, r.name AS repo FROM files f JOIN repos r ON r.id=f.repo_id WHERE f.id=?",
                        (srow["file_id"],)).fetchone()
    out.update(type="symbol", repo=frow["repo"], file_id=frow["id"], path=frow["path"], role=frow["role"],
               symbol_id=srow["id"], symbol=srow["qualname"], kind=srow["kind"])
    return out


def _sym_brief(r) -> dict:
    return {"id": r["id"], "symbol": r["qualname"], "kind": r["kind"], "lines": f"{r['start_line']}-{r['end_line']}"}


# --------------------------------------------------------------------------- #
# find_untested
# --------------------------------------------------------------------------- #
def untested_candidates(conn, repo: str | None = None, path_prefix: str | None = None,
                        min_branchiness: int = 2, min_loc: int = 5, max_tokens: int = 3000,
                        include_module_level: bool = True, skip_io: bool = True,
                        exported_only: bool = True, kinds: tuple = FUNC_KINDS,
                        targetable_only: bool = False, roots: list[str] | None = None,
                        io_mode: str = "strict") -> tuple[list[dict], Counter]:
    """io_mode: 'engine' skips a target with any own IO/nondeterminism tag (the engine's rule);
    'strict' also skips one that reaches tester-hostile IO through same-repo callees (depth 3)."""
    args: list = []
    where = ["f.role = 'src'", f"s.kind IN ({','.join('?' * len(kinds))})"]
    args.extend(kinds)
    if repo:
        rr = _repo_by_alias(conn, repo)
        if rr is None:
            raise TargetError(f"unknown repo {repo}")
        where.append("s.repo_id = ?")
        args.append(rr["id"])
    if path_prefix:
        where.append("f.path LIKE ? ESCAPE '\\'")
        args.append(_like_prefix(path_prefix))
    rows = _rows(conn, f"""
        SELECT s.*, f.path, r.name AS repo, st.calls_same_file, st.calls_same_repo, st.calls_cross_repo,
               st.caller_files, st.caller_repos, st.test_level, st.test_refs, st.name_calls_estate,
               p.qualname AS parent_qual, p.exported AS parent_exported
        FROM symbols s JOIN files f ON f.id = s.file_id JOIN repos r ON r.id = s.repo_id
        JOIN symbol_stats st ON st.symbol_id = s.id LEFT JOIN symbols p ON p.id = s.parent_id
        WHERE {' AND '.join(where)}""", *args)
    skipped: Counter = Counter()
    out = []
    for s in rows:
        io = set((s["io_transitive"] or "").split(",")) - {""}
        own_io = set((s["io"] or "").split(",")) - {""}
        io_hit = own_io or (io_mode == "strict" and io & IO_SKIP)
        reason = None
        if roots is not None and not _under(s["path"], roots):
            reason = "outside roots"
        elif targetable_only and not s["targetable"]:
            reason = "inline (not a splice point)"
        elif s["test_level"] == "direct":
            reason = "tested"
        elif not include_module_level and s["test_level"] == "module":
            reason = "module_tested"
        elif exported_only and not s["exported"]:
            reason = "not_exported"
        elif s["private"]:
            reason = "private"
        elif s["is_generator"]:
            reason = "generator"
        elif s["loc"] < min_loc or s["branchiness"] < min_branchiness:
            reason = "trivial"
        elif s["tokens"] > max_tokens:
            reason = "too_large"
        elif skip_io and io_hit:
            reason = "io"
        if reason:
            skipped[reason] += 1
            continue
        callers = (s["calls_same_file"] or 0) + (s["calls_same_repo"] or 0) + (s["calls_cross_repo"] or 0)
        score = (2 * min(s["branchiness"], 12) + 3 * min(callers, 5) + 2 * s["returns_value"]
                 + (2 if s["calls_cross_repo"] else 0) + (3 if s["test_level"] == "none" else 0))
        out.append({
            "id": s["id"], "repo": s["repo"], "file": s["path"], "symbol": s["qualname"], "kind": s["kind"],
            "lines": f"{s['start_line']}-{s['end_line']}", "loc": s["loc"], "branchiness": s["branchiness"],
            "callers": callers, "other_caller_files": s["caller_files"], "cross_repo_callers": s["calls_cross_repo"],
            "tokens": s["tokens"], "test": s["test_level"], "async": bool(s["is_async"]),
            "inline": bool(s["inline_export"]) or None,
            "io": sorted(io) or None, "score": score,
        })
    out.sort(key=lambda x: (-x["score"], -x["branchiness"], x["repo"], x["file"], x["lines"]))
    return out, skipped


def find_untested(conn, repo: str | None = None, limit: int = 20, offset: int = 0, group_by: str | None = None,
                  **filters) -> dict:
    cands, skipped = untested_candidates(conn, repo=repo, **filters)
    scoring = ("score = 2*min(branchiness,12) + 3*min(runtime callers,5) + 2*returns_value + 2*cross-repo-called"
               " + 3*no-test-at-all; excluded: tested (a test references it), private, generators, trivial "
               "(loc<min_loc or branchiness<min_branchiness), too_large, and IO / nondeterminism: own fs/net/http/pg/"
               "child_process, timers, fetch, process.env/exit, clock, Math.random, or tester-hostile IO "
               "reached through same-repo callees")
    if group_by == "module":
        mods: dict[tuple[str, str], dict] = {}
        for c in cands:
            m = mods.setdefault((c["repo"], c["file"]), {"repo": c["repo"], "file": c["file"], "symbols": [],
                                                         "tokens": 0, "score": 0, "max_branchiness": 0})
            m["symbols"].append(c["symbol"])
            m["tokens"] += c["tokens"]
            m["score"] += c["score"]
            m["max_branchiness"] = max(m["max_branchiness"], c["branchiness"])
        items = sorted(mods.values(), key=lambda m: (-m["score"], m["repo"], m["file"]))
        for m in items:
            m["n"] = len(m["symbols"])
            if len(m["symbols"]) > 12:
                m["symbols"] = m["symbols"][:12] + [f"... +{m['n'] - 12}"]
        res = _page(items, offset, limit)
    else:
        res = _page(cands, offset, limit)
    by_repo = Counter(c["repo"] for c in cands)
    res.update(by_repo=dict(sorted(by_repo.items())), skipped=dict(sorted(skipped.items())), scoring=scoring)
    return res


def _under(path: str, roots: list[str]) -> bool:
    return any(r in (".", "") or path == r.strip("/") or path.startswith(r.strip("/") + "/") for r in roots)


def engine_roots(conn, repo: str) -> list[str]:
    """The engine inventory's default source roots: src/ and lib/ when present, else the whole repo."""
    rr = _repo_by_alias(conn, repo)
    if rr is None:
        return ["."]
    have = [r for r in ("src", "lib") if conn.execute("SELECT 1 FROM files WHERE repo_id=? AND path LIKE ? LIMIT 1",
                                                       (rr["id"], r + "/%")).fetchone()]
    return have or ["."]


def find_undocumented(conn, repo: str | None = None, incomplete: bool = False, kinds: tuple = FUNC_KINDS,
                      path_prefix: str | None = None, targetable_only: bool = False,
                      roots: list[str] | None = None) -> list[dict]:
    """Exported functions/methods without JSDoc (or, with incomplete=True, without complete @param/@returns)."""
    args: list = list(kinds)
    where = ["f.role = 'src'", f"s.kind IN ({','.join('?' * len(kinds))})", "s.exported = 1", "s.private = 0"]
    if repo:
        rr = _repo_by_alias(conn, repo)
        if rr is None:
            raise TargetError(f"unknown repo {repo}")
        where.append("s.repo_id = ?")
        args.append(rr["id"])
    if path_prefix:
        where.append("f.path LIKE ? ESCAPE '\\'")
        args.append(_like_prefix(path_prefix))
    where.append("s.jsdoc_complete = 0" if incomplete else "s.jsdoc = 0")
    if targetable_only:
        where.append("s.targetable = 1")
    rows = _rows(conn, f"""SELECT s.*, f.path, r.name AS repo, st.calls_same_file + st.calls_same_repo +
                           st.calls_cross_repo AS callers FROM symbols s JOIN files f ON f.id=s.file_id
                           JOIN repos r ON r.id=s.repo_id JOIN symbol_stats st ON st.symbol_id=s.id
                           WHERE {' AND '.join(where)} ORDER BY r.name, f.path, s.start_line""", *args)
    if roots is not None:
        rows = [s for s in rows if _under(s["path"], roots)]
    return [{"id": s["id"], "repo": s["repo"], "file": s["path"], "symbol": s["qualname"], "kind": s["kind"],
             "lines": f"{s['start_line']}-{s['end_line']}", "params": len(json.loads(s["params"] or "[]")),
             "callers": s["callers"], "tokens": s["tokens"], "sig_tokens": s["sig_tokens"]} for s in rows]


# --------------------------------------------------------------------------- #
# dependents_of
# --------------------------------------------------------------------------- #
def _pins(conn) -> dict[tuple[str, str], dict]:
    """(consumer repo, target repo) -> pin info from the consumer's root package.json."""
    out = {}
    for r in _rows(conn, """SELECT r.name AS repo, d.* FROM pkg_deps d JOIN repos r ON r.id=d.repo_id
                            WHERE d.package_dir='' AND d.internal=1 AND d.target_repo IS NOT NULL"""):
        out[(r["repo"], r["target_repo"])] = {"package": r["dep"], "section": r["section"], "ref": r["pin_ref"],
                                              "commits_past_pin": r["commits_past_pin"],
                                              "files_changed": r["files_changed"]}
    return out


def dependents_of(conn, target: str, name: str | None = None, repo: str | None = None,
                  include_tests: bool = True, limit: int = 40, offset: int = 0) -> dict:
    tgt = resolve_target(conn, target, repo=repo, symbol=name)
    pins = _pins(conn)
    if tgt["type"] == "repo":
        rows = _rows(conn, """SELECT i.*, f.path, r.name AS repo, tf.path AS tpath FROM imports i
                              JOIN files f ON f.id=i.file_id JOIN repos r ON r.id=i.repo_id
                              LEFT JOIN files tf ON tf.id=i.target_file_id
                              WHERE i.target_repo=? AND r.name != ?""", tgt["repo"], tgt["repo"])
        by_mod: dict[str, dict] = {}
        for i in rows:
            if not include_tests and i["edge"] == "test":
                continue
            m = by_mod.setdefault(i["tpath"] or i["spec"], {"module": i["tpath"] or i["spec"], "files": set(),
                                                             "repos": set(), "names": Counter(), "edges": Counter()})
            m["files"].add(f"{i['repo']}/{i['path']}")
            m["repos"].add(i["repo"])
            m["edges"][i["edge"]] += 1
            for imported, _ in json.loads(i["names"] or "[]"):
                m["names"][imported] += 1
            if i["member"]:
                m["names"][i["member"]] += 1
        mods = sorted(by_mod.values(), key=lambda m: (-len(m["files"]), m["module"]))
        items = [{"module": m["module"], "dependent_files": len(m["files"]), "repos": sorted(m["repos"]),
                  "edges": dict(m["edges"]), "names": [n for n, _ in m["names"].most_common(10)]} for m in mods]
        consumers = sorted({i["repo"] for i in rows})
        res = _page(items, offset, limit)
        res.update(target=tgt, level="package", consumer_repos=consumers,
                   pins={c: pins.get((c, tgt["repo"])) for c in consumers})
        return res

    file_id = tgt["file_id"]
    sym_id = tgt.get("symbol_id")
    hits: dict[int, dict] = {}

    def hit(fid: int) -> dict:
        if fid not in hits:
            f = conn.execute("SELECT f.path, f.role, r.name AS repo FROM files f JOIN repos r ON r.id=f.repo_id "
                             "WHERE f.id=?", (fid,)).fetchone()
            hits[fid] = {"repo": f["repo"], "file": f["path"], "role": f["role"], "lines": set(), "names": set(),
                         "calls": 0, "refs": 0, "via": set()}
        return hits[fid]

    # import edges into the file (and through one re-export hop: barrels)
    barrel_set = {r["file_id"] for r in _rows(conn, "SELECT DISTINCT file_id FROM exports WHERE target_file_id=?",
                                              file_id)} - {file_id}
    for i in _rows(conn, f"""SELECT i.*, (SELECT group_concat(n.imported) FROM import_names n
                             WHERE n.import_id=i.id) AS inames FROM imports i
                             WHERE i.target_file_id IN ({','.join('?' * (1 + len(barrel_set)))})""",
                   file_id, *barrel_set):
        names = set((i["inames"] or "").split(",")) - {""}
        if sym_id is not None:
            sym_names = {tgt["symbol"].split(".")[0], tgt["symbol"]}
            if i["local"] is None and names and not (names & sym_names):
                continue
        h = hit(i["file_id"])
        h["lines"].add(i["line"])
        h["names"] |= names
        h["via"].add("barrel" if i["target_file_id"] in barrel_set else i["kind"])
    if sym_id is not None:
        ids = [sym_id] + [r["id"] for r in _rows(conn, "SELECT id FROM symbols WHERE parent_id=?", sym_id)]
        for r in _rows(conn, f"SELECT file_id, kind, via, line, n FROM refs WHERE callee_symbol_id IN "
                             f"({','.join('?' * len(ids))})", *ids):
            if r["file_id"] == file_id:
                continue
            h = hit(r["file_id"])
            if r["kind"] in ("call", "new"):
                h["calls"] += r["n"]
            else:
                h["refs"] += r["n"]
            if r["line"]:
                h["lines"].add(r["line"])
        # namespace importers that never touch this symbol are dropped
        for fid in [k for k, v in hits.items() if not v["calls"] and not v["refs"] and not
                    (v["names"] & {tgt["symbol"], tgt["symbol"].split(".")[0]})]:
            del hits[fid]
    items = []
    for h in hits.values():
        edge = "test" if h["role"] in ("test", "test_support") else ("tooling" if h["role"] in ("config", "script")
                                                                    else "runtime")
        if not include_tests and edge == "test":
            continue
        items.append({"repo": h["repo"], "file": h["file"], "edge": edge, "cross_repo": h["repo"] != tgt["repo"],
                      "lines": sorted(h["lines"])[:8], "imports": sorted(h["names"])[:10] or None,
                      "calls": h["calls"], "refs": h["refs"], "via": sorted(h["via"]) or None})
    items.sort(key=lambda x: (not x["cross_repo"], x["edge"] != "runtime", x["repo"], x["file"]))
    by_repo: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        by_repo[it["repo"]][it["edge"]] += 1
    res = _page(items, offset, limit)
    consumer_repos = sorted({it["repo"] for it in items if it["cross_repo"]})
    res.update(target=tgt, level="symbol" if sym_id else "module",
               by_repo={k: dict(v) for k, v in sorted(by_repo.items())},
               pins={c: pins.get((c, tgt["repo"])) for c in consumer_repos})
    return res


# --------------------------------------------------------------------------- #
# impact
# --------------------------------------------------------------------------- #
def repo_graph(conn) -> dict:
    nodes = [r["name"] for r in _rows(conn, "SELECT name FROM repos ORDER BY name")]
    all_e: dict[str, set[str]] = defaultdict(set)
    run_e: dict[str, set[str]] = defaultdict(set)
    kinds: dict[tuple[str, str], dict] = {}
    for e in _rows(conn, "SELECT * FROM repo_edges"):
        all_e[e["from_repo"]].add(e["to_repo"])
        if e["kind"] == "runtime" or (e["kind"] == "declared_only" and e["declared_section"] == "dependencies"):
            run_e[e["from_repo"]].add(e["to_repo"])
        kinds[(e["from_repo"], e["to_repo"])] = dict(e)
    return {"nodes": nodes, "all": all_e, "runtime": run_e, "edges": kinds}


def _repo_impact(conn, repo: str, g: dict | None = None) -> dict:
    g = g or repo_graph(conn)
    affected_run, test_only = graph.blast(repo, g["all"], g["runtime"])
    affected_all = set(affected_run) | set(test_only)
    involved = sorted(affected_all | {repo})
    order = graph.topo_order(involved, g["all"])
    cycles = []
    for comp in graph.tarjan_scc(g["nodes"] + sorted({b for bs in g["all"].values() for b in bs}), g["all"]):
        if not set(comp) & set(involved):
            continue
        legs = []
        for a in comp:
            for b in comp:
                if b in g["all"].get(a, ()):
                    legs.append(f"{a}->{b} ({g['edges'][(a, b)]['kind']})")
        runtime_cycle = bool(graph.tarjan_scc(comp, {a: g["runtime"].get(a, set()) & set(comp) for a in comp}))
        cycles.append({"repos": comp, "edges": legs, "runtime_cycle": runtime_cycle})
    rinfo = repos(conn)
    tests = {r: {"test_files": rinfo[r]["test_files"], "has_jest": bool(rinfo[r]["has_jest"])}
             for r in involved if r in rinfo}
    pins = _pins(conn)
    drift = {c: pins[(c, repo)] for c in sorted(affected_all) if (c, repo) in pins and pins[(c, repo)].get("ref")}
    not_in_snapshot = sorted({b for a in involved for b in g["all"].get(a, ())} - set(rinfo))
    return {"affected_runtime": sorted(affected_run, key=lambda r: (affected_run[r], r)),
            "affected_test_only": test_only, "build_test_order": order, "cycles": cycles, "tests": tests,
            "pins_on_target": drift, "deps_not_in_snapshot": not_in_snapshot}


def impact(conn, target: str, repo: str | None = None, symbol: str | None = None, depth: int = 3,
           limit: int = 40) -> dict:
    tgt = resolve_target(conn, target, repo=repo, symbol=symbol)
    g = repo_graph(conn)
    if tgt["type"] == "repo":
        out = {"target": tgt, "level": "repo", **_repo_impact(conn, tgt["repo"], g)}
        out["warnings"] = _warnings(conn, tgt, out, cross_files=None)
        return out

    file_id = tgt["file_id"]
    # reverse import graph over files (runtime + test edges)
    rev: dict[int, set[int]] = defaultdict(set)
    for r in _rows(conn, "SELECT file_id, target_file_id FROM imports WHERE target_file_id IS NOT NULL"):
        rev[r["target_file_id"]].add(r["file_id"])
    role = {r["id"]: r["role"] for r in _rows(conn, "SELECT id, role FROM files")}
    fmeta = {r["id"]: (r["repo"], r["path"]) for r in
             _rows(conn, "SELECT f.id, f.path, r.name AS repo FROM files f JOIN repos r ON r.id=f.repo_id")}
    callers_out = []
    if tgt.get("symbol_id"):
        ids = [tgt["symbol_id"]] + [r["id"] for r in _rows(conn, "SELECT id FROM symbols WHERE parent_id=?",
                                                         tgt["symbol_id"])]
        crow = _rows(conn, f"""SELECT r.file_id, r.caller_symbol_id, sum(r.n) AS n, s.qualname, f.path,
                               rp.name AS repo, f.role FROM refs r JOIN files f ON f.id=r.file_id
                               JOIN repos rp ON rp.id=f.repo_id LEFT JOIN symbols s ON s.id=r.caller_symbol_id
                               WHERE r.callee_symbol_id IN ({','.join('?' * len(ids))})
                               GROUP BY r.file_id, r.caller_symbol_id ORDER BY n DESC""", *ids)
        callers_out = [{"repo": c["repo"], "file": c["path"], "caller": c["qualname"] or "(module scope)",
                        "n": c["n"], "test": c["role"] in ("test", "test_support")} for c in crow]
        direct_files = {c["file_id"] for c in crow} - {file_id}
    else:
        direct_files = set(rev.get(file_id, ())) - {file_id}
    seen: dict[int, int] = {f: 1 for f in direct_files}
    frontier = [f for f in direct_files if role.get(f) not in ("test", "test_support")]
    for d in range(2, max(1, depth) + 1):
        nxt = []
        for f in frontier:
            for p in rev.get(f, ()):
                if p not in seen and p != file_id:
                    seen[p] = d
                    if role.get(p) not in ("test", "test_support"):
                        nxt.append(p)
        frontier = nxt
    affected = [f for f in seen if role.get(f) not in ("test", "test_support")]
    tests = sorted({f for f in seen if role.get(f) == "test"})
    # tests that cover the target itself or any affected file (jest --findRelatedTests analogue)
    cover = {file_id} | set(affected)
    for r in _rows(conn, f"SELECT DISTINCT test_file_id FROM test_links WHERE target_file_id IN "
                         f"({','.join('?' * len(cover))})", *cover):
        tests.append(r["test_file_id"])
    tests = sorted(set(tests))
    by_repo_files: dict[str, list[str]] = defaultdict(list)
    for f in sorted(affected, key=lambda f: (seen[f], fmeta[f])):
        by_repo_files[fmeta[f][0]].append(f"{fmeta[f][1]} (d{seen[f]})")
    tests_by_repo: dict[str, list[str]] = defaultdict(list)
    for t in tests:
        tests_by_repo[fmeta[t][0]].append(fmeta[t][1])
    repos_touched = sorted({fmeta[f][0] for f in affected} | {tgt["repo"]})
    rinfo = repos(conn)
    cross = sorted({fmeta[f][0] for f in affected} - {tgt["repo"]})
    out = {
        "target": tgt, "level": tgt["type"], "depth": depth,
        "direct_dependents": len(direct_files),
        "affected_files": {"total": len(affected),
                           "by_repo": {r: v[:limit] + ([f"... +{len(v) - limit}"] if len(v) > limit else [])
                                       for r, v in sorted(by_repo_files.items())}},
        "callers": callers_out[:limit] if callers_out else None,
        "tests_to_run": {"total": len(tests), "by_repo": {r: v[:limit] for r, v in sorted(tests_by_repo.items())}},
        "repos_touched": repos_touched, "cross_repo_dependents": cross,
        "repos_without_tests": [r for r in repos_touched if r in rinfo and not rinfo[r]["test_files"]],
        "build_test_order": graph.topo_order(repos_touched, g["all"]),
    }
    if tgt.get("symbol_id"):
        s = conn.execute("SELECT * FROM symbols WHERE id=?", (tgt["symbol_id"],)).fetchone()
        out["symbol"] = {"signature": s["signature"], "exported": bool(s["exported"]), "io": s["io_transitive"],
                         "jsdoc": bool(s["jsdoc"]), "branchiness": s["branchiness"]}
    out["warnings"] = _warnings(conn, tgt, out, cross_files=cross)
    return out


def _warnings(conn, tgt: dict, out: dict, cross_files) -> list[str]:
    w = []
    rinfo = repos(conn)
    r = rinfo.get(tgt["repo"])
    if tgt["type"] == "repo":
        if out.get("affected_runtime"):
            w.append(f"{len(out['affected_runtime'])} repo(s) depend on {tgt['repo']} at runtime; a change to its "
                     "exported API is shared-surface work (frontier writes it, never carded)")
    elif cross_files:
        w.append(f"cross-repo surface: dependents in {', '.join(cross_files)}; shared signatures and config APIs "
                 "are on the do-not-decompose list (frontier writes them)")
    for c in out.get("cycles") or []:
        if not c["runtime_cycle"]:
            w.append(f"cycle {' <-> '.join(c['repos'])} is test/tooling-only on one side: {'; '.join(c['edges'])}")
        else:
            w.append(f"runtime cycle {' <-> '.join(c['repos'])}: {'; '.join(c['edges'])}")
    pins = _pins(conn)
    if tgt["type"] == "repo":
        scope = set(out.get("affected_runtime") or []) | set(out.get("affected_test_only") or [])
    else:
        scope = set(cross_files or [])
    drift: dict[tuple, list[str]] = defaultdict(list)
    for (consumer, target_repo), pin in sorted(pins.items()):
        if target_repo == tgt["repo"] and pin.get("commits_past_pin") and consumer in scope:
            drift[(pin["package"], pin["ref"], pin["commits_past_pin"], pin["files_changed"])].append(consumer)
    for (pkg, ref, n, nf), consumers in sorted(drift.items()):
        w.append(f"{', '.join(consumers)} pin {pkg}#{ref}; {tgt['repo']} HEAD is {n} commit(s) / {nf} file(s) "
                 "past it: CI resolves the old tag until the pins are bumped")
    for name in out.get("repos_without_tests") or []:
        w.append(f"{name} has no test files: changes there get static checks only (frontier review)")
    if r is not None and not r["has_jest"] and r["test_script"]:
        w.append(f"{tgt['repo']} declares `{r['test_script']}` but has no jest dependency")
    if out.get("deps_not_in_snapshot"):
        w.append("not in this snapshot (edges known from package.json only): " + ", ".join(out["deps_not_in_snapshot"]))
    return w


# --------------------------------------------------------------------------- #
# Overview (used by REPOS.md and index_status)
# --------------------------------------------------------------------------- #
def overview(conn) -> dict:
    meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    counts = json.loads(meta.get("counts", "{}"))
    return {"snapshot_id": meta.get("snapshot_id"), "built_at": meta.get("built_at"),
            "build_seconds": float(meta.get("build_seconds", 0)), "counted_by": meta.get("counted_by"),
            "repos": json.loads(meta.get("repos", "[]")), "counts": counts}
