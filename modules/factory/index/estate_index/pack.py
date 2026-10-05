"""pack_dry_run: show the cards a sweep would produce and their prompt sizes per GPU lane.

Input is either a generator template (factory/engine/schemas/template.v1.schema.json) or a list
of frontier-written cards (card.v1). Templates are expanded against the index with the
engine's own defaults (group size, per-repo cap, test-file naming, instruction text), so
the preview has the same shape as the engine's cards; budgets use the engine's lane table,
routing ladder, max_tokens and drop order when factory_engine is importable, else the same
values copied from docs/FACTORY-DESIGN.md.

Token counts of code (targets, imports, signatures, values, exemplar) are exact with the
Qwen tokenizer; the prompt frame (system + format rules + headers) is an estimate
(FRAME_TOKENS). The engine re-selects and re-packs at submit, so this is a preview: its
mutation-site filter can drop a few targets this index keeps.

The full result (cards + the payload `submit` sends) is written to <index dir>/packs/<pack_id>.json.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import posixpath
import re
import sqlite3
from collections import Counter
from pathlib import Path

from . import queries
from .tokens import Counter as TokCounter

# Conservative preview frame estimates; execution uses the configured lane budget gates.
FRAME_TOKENS = {"doc_map": 1000, "test_gen": 750}
FRAME_DEFAULT = 750
CALLER_LINE_TOKENS = 40
CTX_MARGIN = 256
_DEFAULT_LANES = {
    "lanes": {
        "lane-gpu-a": {"tier": "local-large", "ctx": 8192, "prompt_cap": 4600},
        "lane-gpu-b": {"tier": "local-small", "ctx": 12288, "prompt_cap": 6900},
    },
    "routing": {
        "doc_map": {"easy": [{"lane": "lane-gpu-b", "steal_to": ["lane-gpu-a"]}, {"lane": "lane-gpu-a"}],
                    "normal": [{"lane": "lane-gpu-a", "steal_to": ["lane-gpu-b"]}, {"lane": "lane-gpu-b"}]},
        "test_gen": {"easy": [{"lane": "lane-gpu-b", "steal_to": ["lane-gpu-a"]}, {"lane": "lane-gpu-a"}],
                     "normal": [{"lane": "lane-gpu-a", "steal_to": ["lane-gpu-b"]}, {"lane": "lane-gpu-b"}]},
    },
}
_MAX_TOKENS = {"doc_map": 1400, "test_gen": 2600}
_DOC_BASE, _DOC_PER = 160, 180
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
# card.v1 "relpath": relative, no drive, no `..` segment, no .git, no backslash or control characters.
RELPATH_RE = re.compile(r"(?![/\\])(?![A-Za-z]:)(?!(?:.*/)?\.\.(?:/|$))(?!(?:.*/)?\.git(?:/|$))[^\\\x00-\x1f]{1,400}")
ROOT_RE = re.compile(r"(?![/\\])(?!.*\.\.)[A-Za-z0-9._/-]{1,200}")       # template.v1 expand.roots items
_DEFAULT_SOURCES = ["src", "lib"]       # factory/engine/profiles/jsdoc-cjs.json "sources"


def _relpath_ok(v) -> bool:
    return isinstance(v, str) and RELPATH_RE.fullmatch(v) is not None


def engine_default_sources() -> list[str]:
    """`sources` of the engine's default profile (its inventory roots when no profile gives any)."""
    try:
        from factory_engine import config as ec  # type: ignore
        prof = json.loads((ec.PROFILES_DIR / "jsdoc-cjs.json").read_text(encoding="utf-8"))
        return [s.strip("/") for s in prof.get("sources") or []] or list(_DEFAULT_SOURCES)
    except Exception:
        return list(_DEFAULT_SOURCES)


def inventory_roots(conn, repo: str, tpl: dict) -> list[str]:
    """The files the engine inventories for this template: an inline profile's `sources` (none: src/ and
    lib/ when present, else the repo), otherwise the default profile's sources. The template's own
    expand.roots only filter inside these. A host `--profile` (FACTORY_ENGINE_SUBMIT_ARGS) with other
    sources is not visible here; engine_check=true shows the engine's own selection."""
    prof = tpl.get("profile")
    if isinstance(prof, dict):
        src = [s.strip("/") for s in prof.get("sources") or [] if isinstance(s, str)]
        return src or queries.engine_roots(conn, repo)
    return engine_default_sources()


def engine_config() -> dict:
    """Lanes / routing / max_tokens from factory_engine.config when importable (single source of truth)."""
    try:
        from factory_engine import config as ec  # type: ignore
        return {"lanes": copy.deepcopy(ec.DEFAULT_LANES), "max_tokens": dict(ec.MAX_TOKENS),
                "doc_base": ec.DOC_TOKENS_BASE, "doc_per": ec.DOC_TOKENS_PER_SYMBOL, "margin": ec.CTX_MARGIN,
                "source": "factory_engine.config"}
    except Exception:
        return {"lanes": copy.deepcopy(_DEFAULT_LANES), "max_tokens": dict(_MAX_TOKENS), "doc_base": _DOC_BASE,
                "doc_per": _DOC_PER, "margin": CTX_MARGIN, "source": "estate_index.pack defaults"}


class PackRequestError(ValueError):
    pass


class _Src:
    """Read-only access to snapshot files for cutting text (never executed)."""

    def __init__(self, conn: sqlite3.Connection, snapshot_dir: Path):
        self.conn = conn
        self.root = snapshot_dir
        self._cache: dict[int, bytes] = {}

    def text(self, file_id: int) -> bytes:
        if file_id not in self._cache:
            r = self.conn.execute("SELECT f.path, r.name FROM files f JOIN repos r ON r.id=f.repo_id WHERE f.id=?",
                                  (file_id,)).fetchone()
            p = (self.root / r["name"] / r["path"]).resolve()
            if self.root.resolve() not in p.parents:
                raise PackRequestError("path escapes the snapshot")
            self._cache[file_id] = p.read_bytes()
        return self._cache[file_id]


def _max_tokens(cfg: dict, kind: str, n: int) -> int:
    if kind == "doc_map":
        return min(cfg["max_tokens"]["doc_map"], cfg["doc_base"] + cfg["doc_per"] * n)
    return cfg["max_tokens"].get(kind, 2000)


def _lanes_for(cfg: dict, kind: str, difficulty: str) -> list[str]:
    ladder = cfg["lanes"]["routing"].get(kind, {}).get(difficulty) or []
    out: list[str] = []
    for rung in ladder:
        for ln in [rung["lane"], *rung.get("steal_to", [])]:
            if ln not in out:
                out.append(ln)
    return out or list(cfg["lanes"]["lanes"])


def _fits(lane: dict, prompt: int, mt: int, margin: int) -> bool:
    return prompt <= int(lane["prompt_cap"]) and prompt + mt <= int(lane["ctx"]) - margin


def _test_file_for(rel: str, k: int, n: int, test_dir: str | None) -> str:
    base = posixpath.basename(rel).rsplit(".", 1)[0]
    suffix = f".factory-{k}" if n > 1 else ".factory"
    d = test_dir.rstrip("/") if test_dir else posixpath.join(posixpath.dirname(rel), "__tests__")
    return posixpath.join(d, f"{base}{suffix}.test.js")


# --------------------------------------------------------------------------- #
# Budget estimate for one card (index + source text)
# --------------------------------------------------------------------------- #
class _Estimator:
    def __init__(self, conn, counter: TokCounter, src: _Src, cfg: dict):
        self.conn = conn
        self.counter = counter
        self.src = src
        self.cfg = cfg
        self._imports: dict[int, int] = {}

    def imports_tokens(self, file_id: int) -> int:
        if file_id not in self._imports:
            text = self.src.text(file_id).decode("utf-8", "replace").splitlines()
            lines = sorted({r["line"] for r in self.conn.execute(
                "SELECT line FROM imports WHERE file_id=? AND lazy=0 AND kind IN ('require','import')", (file_id,))})
            self._imports[file_id] = self.counter.count("\n".join(text[ln - 1] for ln in lines if ln <= len(text)))
        return self._imports[file_id]

    def estimate(self, kind: str, file_id: int, sym_ids: list[int], instruction: str, acceptance: list[str],
                 exemplar_tokens: int, callers_per_symbol: int) -> dict:
        c = self.conn
        rows = [c.execute("SELECT * FROM symbols WHERE id=?", (i,)).fetchone() for i in sym_ids]
        comp: dict[str, int] = {"frame_estimate": FRAME_TOKENS.get(kind, FRAME_DEFAULT),
                                "instruction": self.counter.count(instruction + "\n" + "\n".join(acceptance)),
                                "target": sum(r["tokens"] for r in rows),
                                "imports": self.imports_tokens(file_id)}
        ids = set(sym_ids)
        callee = c.execute(f"""SELECT r.callee_symbol_id AS id, s.kind, s.file_id, s.tokens, s.sig_tokens,
                               s.parent_id, s.qualname, sum(r.n) AS n FROM refs r JOIN symbols s
                               ON s.id=r.callee_symbol_id WHERE r.caller_symbol_id IN ({','.join('?' * len(ids))})
                               GROUP BY r.callee_symbol_id""", tuple(ids)).fetchall()
        same_sigs = values = other_sigs = helpers_full = 0
        helpers_sig = 0
        for x in callee:
            if x["id"] in ids:
                continue
            if x["file_id"] == file_id:
                if x["kind"] == "value":
                    values += x["tokens"]
                elif x["parent_id"] is not None and any(r["parent_id"] == x["parent_id"] for r in rows):
                    helpers_full += x["tokens"]          # this.helper() of the same class: shown in full
                    helpers_sig += x["sig_tokens"]
                elif x["kind"] in ("function", "class", "object"):
                    same_sigs += x["sig_tokens"]
            elif x["kind"] != "value":
                other_sigs += x["sig_tokens"]
        outline = 0
        parents = {r["parent_id"] for r in rows if r["parent_id"] is not None}
        for p in parents:   # class outline: constructor in full + the other methods' signatures
            for m in c.execute("SELECT id, kind, tokens, sig_tokens FROM symbols WHERE parent_id=?", (p,)):
                if m["id"] in ids:
                    continue
                outline += m["tokens"] if m["kind"] == "constructor" else min(m["sig_tokens"], 60)
        comp.update(class_outline=outline, helpers_full=helpers_full, bindings=values, same_file_signatures=same_sigs,
                    other_file_signatures=other_sigs)
        if kind == "doc_map":
            n_callers = sum(min(callers_per_symbol, (c.execute(
                "SELECT calls_same_file+calls_same_repo+calls_cross_repo AS n FROM symbol_stats WHERE symbol_id=?",
                (i,)).fetchone()["n"] or 0)) for i in sym_ids)
            comp["callers"] = n_callers * CALLER_LINE_TOKENS
        comp["exemplar"] = exemplar_tokens
        mt = _max_tokens(self.cfg, kind, len(sym_ids))
        return {"components": comp, "max_tokens": mt, "helpers_sig": helpers_sig,
                "n_callers": comp.get("callers", 0) // CALLER_LINE_TOKENS}

    def fit(self, kind: str, difficulty: str, est: dict) -> dict:
        lanes_cfg = self.cfg["lanes"]["lanes"]
        names = [n for n in _lanes_for(self.cfg, kind, difficulty) if n in lanes_cfg]
        lanes = {n: lanes_cfg[n] for n in names}
        pack_cap = min(int(ln["prompt_cap"]) for ln in lanes.values())
        min_ctx = min(int(ln["ctx"]) for ln in lanes.values())
        margin = self.cfg["margin"]
        comp = dict(est["components"])
        mt = est["max_tokens"]
        dropped: list[str] = []
        steps = [
            ("class_outline: helper bodies -> signatures",
             lambda c_: c_.update(helpers_full=est["helpers_sig"])),
            ("call sites -> 1 per symbol",
             lambda c_: c_.update(callers=min(c_.get("callers", 0), CALLER_LINE_TOKENS * len(est.get("syms", [1]))))),
            ("call sites dropped", lambda c_: c_.update(callers=0)),
            ("module values -> head", lambda c_: c_.update(bindings=c_["bindings"] // 3)),
            ("module values -> one line", lambda c_: c_.update(bindings=min(c_["bindings"], 30))),
            ("other files' signatures dropped", lambda c_: c_.update(other_file_signatures=0)),
            ("exemplar dropped", lambda c_: c_.update(exemplar=0)),
        ]
        total = sum(comp.values())
        i = 0
        while not (total <= pack_cap and total + mt <= min_ctx - margin) and i < len(steps):
            label, fn = steps[i]
            before = dict(comp)
            fn(comp)
            if comp != before:
                dropped.append(label)
            total = sum(comp.values())
            i += 1
        fit = [n for n, ln in lanes.items() if _fits(ln, total, mt, margin)]
        return {"prompt_tokens": total, "max_tokens": mt, "pack_cap": pack_cap, "lane_fit": fit,
                "dropped": dropped, "components": comp, "status": "ok" if fit else "TOO_LARGE",
                "ladder_lanes": names}


# --------------------------------------------------------------------------- #
# Template expansion against the index
# --------------------------------------------------------------------------- #
def _select(conn, tpl: dict) -> tuple[list[dict], dict]:
    ex = tpl["expand"]
    flt = ex.get("filter") or {}
    kinds_in = set(flt.get("kinds") or ["function", "arrow", "method", "constructor"])
    kinds = tuple(k for k in ("function", "method", "constructor") if k in kinds_in or
                  (k == "function" and "arrow" in kinds_in))
    import fnmatch
    inc, exc, only = ex.get("include"), ex.get("exclude"), set(flt.get("symbols") or [])
    roots = inventory_roots(conn, ex["repo"], tpl)
    tpl_roots = [r.strip("/") for r in ex.get("roots") or []]
    max_loc = int(flt.get("max_loc", 160))
    min_params = int(flt.get("min_params", 0))
    skipped: Counter = Counter()
    if tpl["kind"] == "doc_map":
        cands = queries.find_undocumented(conn, repo=ex["repo"], kinds=kinds, targetable_only=True, roots=roots)
        rows = []
        for cnd in cands:
            if cnd["kind"] == "constructor" and not cnd["params"]:
                skipped["constructor without params"] += 1
                continue
            rows.append(cnd)
    else:
        rows, sk = queries.untested_candidates(
            conn, repo=ex["repo"], min_branchiness=int(flt.get("min_branches", 2)),
            min_loc=int(flt.get("min_loc", 5)), skip_io=bool(flt.get("skip_io", True)),
            exported_only=bool(flt.get("exported_only", True)), kinds=tuple(k for k in kinds if k != "constructor"),
            max_tokens=10 ** 9, targetable_only=True, roots=roots, io_mode="engine")
        skipped.update(sk)
    out = []
    for r in rows:
        rel = r["file"]
        if tpl_roots and not queries._under(rel, tpl_roots):
            skipped["outside expand.roots"] += 1
            continue
        if inc and not any(fnmatch.fnmatchcase(rel, g) for g in inc):
            skipped["not included"] += 1
            continue
        if exc and any(fnmatch.fnmatchcase(rel, g) for g in exc):
            skipped["excluded"] += 1
            continue
        if only and r["symbol"] not in only and f"{rel}:{r['symbol']}" not in only:
            continue
        a, b = (int(x) for x in r["lines"].split("-"))
        if b - a + 1 > max_loc:
            skipped["too large"] += 1
            continue
        if min_params and r.get("params", min_params) < min_params:
            skipped["too few params"] += 1
            continue
        out.append(r)
    return out, dict(skipped)


def _groups(conn, targets: list[dict], max_symbols: int, max_chars: int) -> list[tuple[str, str, list[dict]]]:
    by_file: dict[tuple[str, str], list[dict]] = {}
    for t in targets:
        by_file.setdefault((t["repo"], t["file"]), []).append(t)
    out = []
    for (repo, rel) in sorted(by_file):
        cur, size = [], 0
        for s in sorted(by_file[(repo, rel)], key=lambda x: int(x["lines"].split("-")[0])):
            row = conn.execute("SELECT end_byte - start_byte AS chars FROM symbols WHERE id=?", (s["id"],)).fetchone()
            ch = row["chars"]
            if cur and (len(cur) >= max_symbols or size + ch > max_chars):
                out.append((repo, rel, cur))
                cur, size = [], 0
            cur.append(s)
            size += ch
        if cur:
            out.append((repo, rel, cur))
    return out


def _doc_texts(tpl_card: dict, kind: str, listed: str, rel: str, test_file: str | None) -> tuple[str, list[str]]:
    if kind == "doc_map":
        return (tpl_card.get("instruction") or
                f"Write one JSDoc block for each of: {listed} (in {rel}). Follow DOC RULES. "
                "Types must hold under tsc --checkJs against the code and the call sites shown.",
                tpl_card.get("acceptance") or [
                    "Keys are exactly the listed symbols; each value is a single /** ... */ block.",
                    "Every formal parameter has a typed @param with the same name and order; value-returning and "
                    "async functions have @returns.",
                    "Splicing the blocks above the declarations introduces 0 new tsc --checkJs errors in the repo."])
    return (tpl_card.get("instruction") or
            f"Write a new jest test file {test_file} for {listed} (in {rel}). Characterize the current "
            "behaviour through the module's exports; cover the branches shown.",
            tpl_card.get("acceptance") or [
                "All tests pass on the current code; no .only/.skip; no snapshots.",
                "Every test asserts a concrete value.",
                "At least 2 of 3 seeded faults per target make a test fail."])


def _str_list(v, what: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise PackRequestError(f"{what} must be a list of strings")
    return v


def _dict_or_none(v, what: str) -> dict:
    if v is not None and not isinstance(v, dict):
        raise PackRequestError(f"{what} must be an object")
    return v or {}


def _validate_template(tpl: dict) -> None:
    if not isinstance(tpl, dict) or tpl.get("template") != 1:
        raise PackRequestError("template must be an object with template: 1 (template.v1)")
    if not ID_RE.fullmatch(str(tpl.get("template_id", ""))):
        raise PackRequestError("template_id must match ^[a-z0-9][a-z0-9-]{1,40}$")
    if tpl.get("kind") not in ("doc_map", "test_gen"):
        raise PackRequestError("kind must be doc_map or test_gen (the kinds the engine implements)")
    ex = tpl.get("expand")
    if not isinstance(ex, dict) or not isinstance(ex.get("repo"), str) or not ex.get("repo"):
        raise PackRequestError("expand.repo is required")
    # Shape checks the engine's schema would do; they must hold here too, where factory_engine is usually
    # not importable (a malformed template is a PackRequestError, never a TypeError inside expansion).
    for key in ("include", "exclude"):
        if ex.get(key) is not None:
            _str_list(ex[key], f"expand.{key}")
    _dict_or_none(ex.get("filter"), "expand.filter")
    _dict_or_none(ex.get("group"), "expand.group")
    if ex.get("cap") is not None and (isinstance(ex["cap"], bool) or not isinstance(ex["cap"], int) or ex["cap"] < 1):
        raise PackRequestError("expand.cap must be a positive integer")
    card = _dict_or_none(tpl.get("card"), "card")
    _dict_or_none(card.get("tests"), "card.tests")
    _dict_or_none(card.get("retry"), "card.retry")
    _dict_or_none(tpl.get("exemplar"), "exemplar")
    # Paths the engine would open on its host: checked here too, so they hold without factory_engine.
    if "profile" in tpl and not isinstance(tpl["profile"], dict):
        raise PackRequestError("profile: give the profile.v1 object inline. A profile file path is opened on the "
                               "engine host relative to the submitted file, so it is not accepted here; host "
                               "profiles must be inline objects in templates")
    roots = ex.get("roots")
    if roots is not None and (not isinstance(roots, list) or
                              not all(isinstance(r, str) and ROOT_RE.fullmatch(r) for r in roots)):
        raise PackRequestError("expand.roots: relative directories inside the repo (no '..', no leading '/')")
    test_dir = (card.get("tests") or {}).get("test_dir")
    if test_dir is not None and not _relpath_ok(test_dir):
        raise PackRequestError("card.tests.test_dir must be a relative path inside the repo (no '..', no drive)")
    prof = tpl.get("profile") or {}
    jest = _dict_or_none(prof.get("jest"), "profile.jest")
    tsc = _dict_or_none(prof.get("tsc"), "profile.tsc")
    for holder in (tpl, prof, jest):
        exf = (holder.get("exemplar") or {}).get("file") if isinstance(holder.get("exemplar"), dict) else None
        if exf is not None and not _relpath_ok(exf):
            raise PackRequestError("exemplar.file must be a relative path (no '..', no drive); or use exemplar.text")
    # The engine inventories `profile.sources` with path.join(repoDir, s) and the gate opens the jest / tsc
    # paths relative to the repo or its support dir: a `..` there would read outside the repo on that host.
    if prof.get("sources") is not None:
        for s in _str_list(prof["sources"], "profile.sources"):
            if not ROOT_RE.fullmatch(s):
                raise PackRequestError("profile.sources: relative directories inside the repo (no '..', no leading '/')")
    gate_paths = [("profile.jest.setupFiles", jest.get("setupFiles")), ("profile.tsc.include", tsc.get("include")),
                  ("profile.tsc.exclude", tsc.get("exclude"))]
    if jest.get("files") is not None:
        gate_paths.append(("profile.jest.files keys", list(_dict_or_none(jest["files"], "profile.jest.files"))))
    if jest.get("baseJestConfig") is not None:
        gate_paths.append(("profile.jest.baseJestConfig", [jest["baseJestConfig"]]))
    for what, paths in gate_paths:
        if paths is None:
            continue
        for p in _str_list(paths, what):
            if not _relpath_ok(p):
                raise PackRequestError(f"{what}: relative paths inside the repo only (no '..', no drive, no leading '/')")
    want = "find_undocumented" if tpl["kind"] == "doc_map" else "find_untested"
    ex.setdefault("via", want)
    if ex["via"] != want:
        raise PackRequestError(f"kind {tpl['kind']} expands via {want}")
    try:   # the engine's own schema, when it is importable
        from factory_engine.schema import errors as schema_errors  # type: ignore
        errs = schema_errors("template.v1", tpl)
        if errs:
            raise PackRequestError("template.v1: " + "; ".join(errs[:6]))
    except ImportError:
        pass


def _card_schema_errors(card: dict) -> list[str]:
    try:
        from factory_engine.schema import errors as schema_errors  # type: ignore
        return schema_errors("card.v1", card)
    except ImportError:
        return []


def _exemplar_tokens(counter: TokCounter, conn, src: _Src, ex: dict | None, repo: str) -> int:
    if not ex:
        return 0
    if ex.get("text"):
        return counter.count(ex["text"])
    if ex.get("file"):
        rr = conn.execute("SELECT id FROM repos WHERE name=?", (repo,)).fetchone()
        f = conn.execute("SELECT id, tokens FROM files WHERE repo_id=? AND path=?",
                         (rr["id"], ex["file"].strip("/"))).fetchone() if rr else None
        if f is not None:
            if ex.get("symbol"):
                s = conn.execute("SELECT tokens FROM symbols WHERE file_id=? AND qualname=?",
                                 (f["id"], ex["symbol"])).fetchone()
                if s is not None:
                    return s["tokens"]
            return f["tokens"] or counter.count(src.text(f["id"]).decode("utf-8", "replace"))
    return 0


def pack_dry_run(conn: sqlite3.Connection, counter: TokCounter, snapshot_dir: Path, packs_dir: Path | None,
                 template: dict | None = None, cards: list[dict] | None = None, limit: int = 20,
                 offset: int = 0) -> dict:
    if (template is None) == (cards is None):
        raise PackRequestError("give exactly one of template (template.v1) or cards (list of card.v1)")
    cfg = engine_config()
    src = _Src(conn, snapshot_dir)
    est = _Estimator(conn, counter, src, cfg)
    meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")}
    sid = meta.get("snapshot_id")
    manifest_sha256 = meta.get("manifest_sha256") or ""
    request = {"template": template} if template is not None else {"cards": cards}
    pack_id = "p-" + hashlib.sha256(
        (sid + manifest_sha256 + json.dumps(request, sort_keys=True)).encode()
    ).hexdigest()[:12]
    task_id = hashlib.sha256(pack_id.encode()).hexdigest()[:16]     # placeholder; the engine assigns the task
    out_cards: list[dict] = []
    rows: list[dict] = []
    report: dict = {}
    submit_payload: dict

    if template is not None:
        tpl = copy.deepcopy(template)
        _validate_template(tpl)
        kind = tpl["kind"]
        base = tpl.get("card") or {}
        difficulty = base.get("difficulty", "easy")
        rr = queries._repo_by_alias(conn, tpl["expand"]["repo"])
        if rr is None:
            raise PackRequestError(f"repo {tpl['expand']['repo']} is not in snapshot {sid}")
        tpl["expand"]["repo"] = rr["name"]
        targets, skipped = _select(conn, tpl)
        grp = tpl["expand"].get("group") or {}
        groups = _groups(conn, targets, int(grp.get("max_symbols", 4 if kind == "doc_map" else 2)),
                         int(grp.get("max_chars", 3600)))
        cap = int(tpl["expand"].get("cap", 100))
        capped = max(0, len(groups) - cap)
        groups = groups[:cap]
        ex_tok = _exemplar_tokens(counter, conn, src, tpl.get("exemplar"), rr["name"])
        callers = int(base.get("callers_per_symbol", 4))
        per_file = Counter(rel for _, rel, _ in groups)
        seen_file: Counter = Counter()
        for i, (_repo, rel, syms) in enumerate(groups, 1):
            qn = [s["symbol"] for s in syms]
            uid = f"{tpl['template_id']}-{i:03d}"
            listed = ", ".join(f"`{q}`" for q in qn)
            test_file = None
            if kind == "test_gen":
                seen_file[rel] += 1
                test_file = _test_file_for(rel, seen_file[rel], per_file[rel], (base.get("tests") or {}).get("test_dir"))
            instruction, acceptance = _doc_texts(base, kind, listed, rel, test_file)
            file_id = conn.execute("SELECT id FROM files WHERE repo_id=? AND path=?", (rr["id"], rel)).fetchone()["id"]
            e = est.estimate(kind, file_id, [s["id"] for s in syms], instruction, acceptance, ex_tok, callers)
            e["syms"] = qn
            b = est.fit(kind, difficulty, e)
            text = src.text(file_id)
            spans = [conn.execute("SELECT start_byte, end_byte FROM symbols WHERE id=?", (s["id"],)).fetchone()
                     for s in syms]
            slice_sha = hashlib.sha256(b"\n".join(text[a:z] for a, z in spans)).hexdigest()
            card = {
                "card": 1, "unit_id": uid, "task_id": task_id, "snapshot": sid, "repo": rr["name"], "group": uid,
                "risk": base.get("risk", "R0"), "kind": kind, "difficulty": difficulty,
                "target": {"file": rel, "symbols": qn, "slice_sha256": slice_sha},
                "instruction": instruction, "acceptance": acceptance, "deps": [],
                "retry": {"max_generations": min(int((base.get("retry") or {}).get("max_generations", 3)), 3)},
                "priority": int(base.get("priority", 0)),
                "budget": {k: b[k] for k in ("prompt_tokens", "max_tokens", "pack_cap", "lane_fit", "dropped")}
                | {"counted_by": counter.counted_by},
                "provenance": {"author": f"generator:{tpl['template_id']}", "packer": "estate_index-dryrun-1",
                               "template_id": tpl["template_id"],
                               "selection": [{"qname": s["symbol"], "loc": s.get("loc"),
                                              "branches": s.get("branchiness"), "callers": s.get("callers")}
                                             for s in syms]},
            }
            if kind == "doc_map":
                card["target"]["anchor"] = {"mode": "before_symbol"}
                card.update(provides={"doc_symbols": qn, "export": False},
                            constraints={"output": "JSON object only", "signature": "locked (comments only)",
                                         "touch_only": "doc comments of the listed symbols"},
                            output="doc_map_json", verify="docs_tsc", tests={"files": [], "min_tests": 0})
            else:
                card.update(provides={"test_file": test_file, "export": False},
                            constraints={"output": "one complete jest file", "touch_only": test_file},
                            output="new_file", verify="tests",
                            tests={"files": [test_file],
                                   "min_tests": int((base.get("tests") or {}).get("min_tests", max(2, len(qn)))),
                                   "kill_check": True})
            if tpl.get("exemplar"):
                card["exemplar"] = {k: v for k, v in tpl["exemplar"].items() if k in ("file", "text")}
            out_cards.append(card)
            rows.append({"unit_id": uid, "file": rel, "symbols": qn, "prompt_tokens": b["prompt_tokens"],
                         "max_tokens": b["max_tokens"], "lane_fit": b["lane_fit"], "status": b["status"],
                         "dropped": b["dropped"] or None, "components": b["components"]})
        pinned = copy.deepcopy(tpl)
        pinned["expand"].setdefault("filter", {})["symbols"] = sorted(
            {f"{rel}:{q}" for c in out_cards for rel, q in [(c["target"]["file"], s) for s in c["target"]["symbols"]]})
        if len(pinned["expand"]["filter"]["symbols"]) > 500:
            pinned["expand"]["filter"]["symbols"] = pinned["expand"]["filter"]["symbols"][:500]
        submit_payload = {"template": pinned}
        report = {"selected_targets": len(targets), "units": len(out_cards), "capped": capped, "skipped": skipped}
    else:
        if not isinstance(cards, list) or not cards:
            raise PackRequestError("cards must be a non-empty list of card.v1 objects")
        if len(cards) > 500:
            raise PackRequestError("at most 500 cards per dry run")
        sources = engine_default_sources()
        forward: list[dict] = []
        for raw in cards:
            card = copy.deepcopy(raw) if isinstance(raw, dict) else {}
            uid = str(card.get("unit_id", "?"))
            t = card.get("target") if isinstance(card.get("target"), dict) else {}
            syms = t.get("symbols") or ([t["symbol"]] if t.get("symbol") else [])
            kind = card.get("kind", "?")
            row = {"unit_id": uid, "file": t.get("file"), "symbols": syms}
            try:
                rr = queries._repo_by_alias(conn, str(card.get("repo", "")))
                if rr is None:
                    raise PackRequestError(f"repo {card.get('repo')!r} not in snapshot")
                if not _relpath_ok(t.get("file")):
                    raise PackRequestError("TARGET_MISSING: target.file must be a relative path inside the repo")
                exf = (card.get("exemplar") or {}).get("file") if isinstance(card.get("exemplar"), dict) else None
                if exf is not None and not _relpath_ok(exf):
                    raise PackRequestError("exemplar.file must be a relative path (no '..', no drive)")
                f = conn.execute("SELECT id FROM files WHERE repo_id=? AND path=?",
                                 (rr["id"], str(t.get("file", "")).strip("/"))).fetchone()
                if f is None:
                    raise PackRequestError(f"TARGET_MISSING: {t.get('file')} not in {rr['name']}")
                ids = []
                for q in syms:
                    s = conn.execute("SELECT id FROM symbols WHERE file_id=? AND qualname=?", (f["id"], q)).fetchone()
                    if s is None:
                        raise PackRequestError(f"TARGET_MISSING: {q} not in {t.get('file')}")
                    ids.append(s["id"])
                if not ids:
                    raise PackRequestError("target.symbols is empty")
                ex_tok = _exemplar_tokens(counter, conn, src, card.get("exemplar"), rr["name"])
                e = est.estimate(kind, f["id"], ids, str(card.get("instruction", "")),
                                 list(card.get("acceptance") or []), ex_tok, 4)
                e["syms"] = syms
                b = est.fit(kind, card.get("difficulty", "normal"), e)
                errs = _card_schema_errors(card)
                st = b["status"] if not errs else "SCHEMA"
                rel = str(t.get("file", "")).strip("/")
                if st == "ok" and not queries._under(rel, sources):
                    st = "OUTSIDE_ROOTS"
                    row["note"] = (f"the engine inventories {', '.join(s + '/' for s in sources)} unless the host "
                                   "profile sets other sources; it answers TARGET_MISSING otherwise")
                row.update(prompt_tokens=b["prompt_tokens"], max_tokens=b["max_tokens"], lane_fit=b["lane_fit"],
                           status=st, dropped=b["dropped"] or None,
                           components=b["components"], schema_errors=errs[:4] or None)
            except PackRequestError as exc:
                row.update(status="REJECTED", reason=str(exc))
            out_cards.append(card)
            rows.append(row)
            if row["status"] != "REJECTED":       # not in this snapshot: never handed to the engine
                forward.append(card)
        submit_payload = {"cards": forward}
        report = {"units": len(out_cards), "submittable": len(forward)}

    ok_rows = [r for r in rows if r.get("status") == "ok"]
    ptoks = sorted(r["prompt_tokens"] for r in rows if "prompt_tokens" in r)

    def pct(p: float) -> int | None:
        return ptoks[min(len(ptoks) - 1, int(math.ceil(p * len(ptoks))) - 1)] if ptoks else None

    lane_fit = Counter(ln for r in ok_rows for ln in r["lane_fit"])
    status = Counter(r.get("status") for r in rows)
    lanes_view = {n: {k: ln.get(k) for k in ("tier", "ctx", "prompt_cap")} for n, ln in cfg["lanes"]["lanes"].items()}
    summary = {
        "cards": len(rows), "status": dict(status), "fits_lane": dict(lane_fit),
        "no_context_dropped": sum(1 for r in ok_rows if not r.get("dropped")),
        "prompt_tokens": {"min": ptoks[0] if ptoks else None, "p50": pct(0.5), "p90": pct(0.9),
                          "max": ptoks[-1] if ptoks else None},
        "total_prompt_tokens": sum(ptoks), "total_max_tokens": sum(r.get("max_tokens", 0) for r in ok_rows),
        "dropped_steps": dict(Counter(d for r in rows for d in (r.get("dropped") or []))),
        "counted_by": counter.counted_by, "frame_tokens_estimate": FRAME_TOKENS, "budget_rules": cfg["source"],
        "note": "preview: code tokens exact, frame estimated; the engine re-selects and re-packs at "
                "submit; engine admission is authoritative",
    }
    result = {"pack_id": pack_id, "snapshot": sid, "manifest_sha256": manifest_sha256,
              "source": "template" if template is not None else "cards",
              "lanes": lanes_view, "summary": summary, **report}
    holders = [template] if template is not None else [c for c in cards if isinstance(c, dict)]
    if any(isinstance(h.get("exemplar"), dict) and h["exemplar"].get("file") and not h["exemplar"].get("text")
           for h in holders):
        result["warnings"] = ["exemplar.file: this preview counted that file in the snapshot, but the engine reads "
                              "exemplar.file from the profile's directory (factory_engine.packer.exemplar_text) and "
                              "drops the exemplar when it is not there; send exemplar.text instead"]
    page = queries._page(rows, offset, limit)
    result.update(cards=page["items"], total=page["total"], offset=page["offset"], next_offset=page["next_offset"])
    if packs_dir is not None:
        packs_dir.mkdir(parents=True, exist_ok=True)
        pf = packs_dir / f"{pack_id}.json"
        pf.write_text(json.dumps({"pack_id": pack_id, "snapshot": sid, "manifest_sha256": manifest_sha256,
                                  "task_id_placeholder": task_id,
                                  "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                                  "request": request, "submit": submit_payload, "cards": out_cards, "rows": rows,
                                  "summary": summary}, indent=1), encoding="utf-8")
        result["pack_file"] = str(pf)
    return result
