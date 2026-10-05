"""Card validation (JSON Schema + the design's validity rules) and generator expansion.

A card is valid only if (design, "A CARD IS VALID ONLY IF ALL OF THESE HOLD"):
- it touches one span in one file, or creates one new file;
- its context fits as signatures (checked by the packer);
- the instruction is at most 120 words and leaves no design choice open;
- a deterministic check exists that runs in under 60 s (verify: docs_tsc / tests);
- it changes no contract beyond its declared `provides`.

Generator templates (template.v1) expand deterministically against the repo inventory, with no
frontier tokens per unit: `find_undocumented` for doc_map, `find_untested` for test_gen.
"""
from __future__ import annotations

import copy
import fnmatch
import re
from pathlib import PurePosixPath

from . import config
from .packer import Repo, derive_context
from .schema import errors as schema_errors

MAX_INSTRUCTION_WORDS = 120

# Files local models may never touch (design G2 scope): tests, manifests, lockfiles, tool configs, CI.
PROTECTED_RE = re.compile(
    r"(^|/)(package(-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|\.npmrc|\.yarnrc(\.yml)?"
    r"|jest\.config\.[cm]?js|babel\.config\.[cm]?js|\.babelrc|tsconfig[^/]*\.json|Dockerfile[^/]*"
    r"|\.eslintrc[^/]*|\.gitattributes|\.gitmodules)$"
    r"|(^|/)\.(github|gitlab|circleci|husky|devcontainer|vscode|claude|codex)/|(^|/)\.mcp\.json$")
TEST_RE = re.compile(r"(^|/)(__tests__|__mocks__)/|\.(test|spec)\.[cm]?js$")
# Hidden path segments (.aa, .factory, .git in any case, ...) and node_modules: aa refuses a bundle
# with a .aa/ entry, the gate's run copy skips .factory/node_modules, and jest never runs node_modules tests.
HIDDEN_SEG_RE = re.compile(r"(^|/)(\.[^/]*|node_modules)(/|$)", re.I)


class CardError(ValueError):
    def __init__(self, unit_id: str, code: str, message: str):
        super().__init__(f"{unit_id}: {code}: {message}")
        self.unit_id = unit_id
        self.code = code
        self.message = message


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def validate_card(card: dict, flags: set[str], repo: Repo | None = None) -> None:
    """Raise CardError for an invalid card. The packer checks targets and budgets afterwards."""
    uid = str(card.get("unit_id", "?")) if isinstance(card, dict) else "?"
    errs = schema_errors("card.v1", card)
    if errs:
        raise CardError(uid, "SCHEMA", "; ".join(errs[:6]))
    kind = card["kind"]
    if kind not in config.IMPLEMENTED_KINDS:
        raise CardError(uid, "KIND_NOT_IMPLEMENTED", f"kind {kind} is not implemented by this engine "
                        f"(implemented: {', '.join(config.IMPLEMENTED_KINDS)})")
    if kind == "test_gen" and config.FLAG_TEST_GEN not in flags:
        raise CardError(uid, "FLAG_OFF", "test_gen is behind the test_gen feature flag "
                        "(--enable test_gen or FACTORY_ENABLE_TEST_GEN=1); it needs an isolated gate runner")
    n = words(card["instruction"])
    if n > MAX_INSTRUCTION_WORDS:
        raise CardError(uid, "INSTRUCTION_TOO_LONG", f"{n} words (max {MAX_INSTRUCTION_WORDS})")
    rel = card["target"]["file"]
    if HIDDEN_SEG_RE.search(rel):
        raise CardError(uid, "PROTECTED_FILE", f"{rel}: hidden directories and node_modules are out of scope")
    if PROTECTED_RE.search(rel):
        raise CardError(uid, "PROTECTED_FILE", f"{rel} is a protected file (manifest/lockfile/config/CI)")
    if TEST_RE.search(rel):
        raise CardError(uid, "PROTECTED_FILE", f"{rel} is a test file; local units may not edit tests")
    if not rel.endswith((".js", ".cjs")):
        raise CardError(uid, "UNSUPPORTED_FILE", f"{rel}: only .js/.cjs targets are supported")
    if card.get("target", {}).get("symbol") and card["target"].get("symbols"):
        raise CardError(uid, "SCHEMA", "give target.symbol or target.symbols, not both")
    if kind == "test_gen":
        tf = card["provides"]["test_file"]
        if not TEST_RE.search(tf) or PROTECTED_RE.search(tf) or not tf.endswith((".js", ".cjs")):
            raise CardError(uid, "BAD_TEST_FILE", f"{tf} must be a new *.test.js / __tests__ file")
        if HIDDEN_SEG_RE.search(tf):
            raise CardError(uid, "BAD_TEST_FILE", f"{tf}: test files may not live in hidden directories or node_modules")
        if repo is not None and (repo.root / tf).exists():
            raise CardError(uid, "BAD_TEST_FILE", f"{tf} already exists; test_gen only creates new files")
        if (card.get("tests") or {}).get("min_tests", 0) < 1:
            raise CardError(uid, "SCHEMA", "test_gen needs tests.min_tests >= 1 (defeats --passWithNoTests)")
    if repo is not None and card["repo"] != repo.name:
        raise CardError(uid, "REPO_MISMATCH", f"card repo {card['repo']} != {repo.name}")


def normalize(card: dict) -> dict:
    """target.symbol -> target.symbols; defaults for retry."""
    card = copy.deepcopy(card)
    t = card["target"]
    if "symbols" not in t and t.get("symbol"):
        t["symbols"] = [t.pop("symbol")]
    retry = card.setdefault("retry", {})
    retry.setdefault("max_generations", config.MAX_GENERATIONS)
    retry["max_generations"] = min(int(retry["max_generations"]), config.MAX_GENERATIONS)
    return card


# --------------------------------------------------------------------------- generator
def _glob_ok(rel: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    if include and not any(fnmatch.fnmatchcase(rel, g) for g in include):
        return False
    if exclude and any(fnmatch.fnmatchcase(rel, g) for g in exclude):
        return False
    return True


def _tested_names(repo: Repo) -> set[str]:
    """Identifiers that appear in the repo's existing test files (a cheap 'already tested' signal)."""
    names: set[str] = set()
    for p in repo.root.rglob("*"):
        rel = p.relative_to(repo.root).as_posix()
        if "node_modules/" in rel or not p.is_file() or not TEST_RE.search(rel):
            continue
        try:
            names.update(re.findall(r"[A-Za-z_$][\w$]*", p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return names


def select_targets(tpl: dict, repo: Repo) -> tuple[list[dict], dict]:
    """Deterministic target selection. Returns ([{file, sym}], skip counts)."""
    ex = tpl["expand"]
    flt = ex.get("filter") or {}
    kinds = set(flt.get("kinds") or ["function", "arrow", "method", "constructor"])
    exported_only = flt.get("exported_only", True)
    include_private = flt.get("include_private", False)
    min_params = int(flt.get("min_params", 0))
    only = set(flt.get("symbols") or [])
    via = ex["via"]
    tested = _tested_names(repo) if via == "find_untested" else set()
    skips: dict[str, int] = {}

    def skip(why: str) -> None:
        skips[why] = skips.get(why, 0) + 1

    roots = [r.strip("/").rstrip("/") for r in ex.get("roots") or []]
    out = []
    for rel in sorted(repo.files):
        if not _glob_ok(rel, ex.get("include"), ex.get("exclude")) or TEST_RE.search(rel) or PROTECTED_RE.search(rel):
            continue
        if roots and not any(r in (".", "") or rel == r or rel.startswith(r + "/") for r in roots):
            continue
        f = repo.files[rel]
        counts: dict[str, int] = {}
        for s in f["symbols"]:
            counts[s["qname"]] = counts.get(s["qname"], 0) + 1
        for s in f["symbols"]:
            q = s["qname"]
            if only and q not in only and f"{rel}:{q}" not in only:
                continue
            if s["kind"] in ("get", "set"):
                skip("getter/setter")
                continue
            if s["kind"] not in kinds:
                skip("kind filtered")
                continue
            if counts[q] > 1:
                skip("ambiguous name")
                continue
            if exported_only and not s["exported"]:
                skip("not exported")
                continue
            if s["private"] and not include_private:
                skip("private")
                continue
            if len(s["params"]) < min_params:
                skip("too few params")
                continue
            if via == "find_undocumented":
                if s.get("jsdoc"):
                    skip("already documented")
                    continue
                if s["kind"] == "constructor" and not s["params"]:
                    skip("constructor without params")
                    continue
                if not repo.starts_line(rel, s["start"]):
                    skip("declaration shares its line")
                    continue
            else:  # find_untested
                if s["kind"] == "constructor":
                    skip("constructor")
                    continue
                if flt.get("skip_io", True) and s.get("io"):
                    skip("io/nondeterministic")
                    continue
                if s["branchiness"] < int(flt.get("min_branches", 2)):
                    skip("trivial (branches)")
                    continue
                if s["loc"] < int(flt.get("min_loc", 5)):
                    skip("trivial (loc)")
                    continue
                if len(s.get("mutants") or []) < 3:
                    skip("fewer than 3 mutation sites")
                    continue
                if s["name"] in tested:
                    skip("already referenced by a test")
                    continue
            if s["loc"] > int(flt.get("max_loc", 160)):
                skip("too large")
                continue
            out.append({"file": rel, "sym": s})
    return out, skips


def _groups(targets: list[dict], max_symbols: int, max_chars: int) -> list[tuple[str, list[dict]]]:
    by_file: dict[str, list[dict]] = {}
    for t in targets:
        by_file.setdefault(t["file"], []).append(t["sym"])
    out = []
    for rel in sorted(by_file):
        cur: list[dict] = []
        size = 0
        for s in sorted(by_file[rel], key=lambda x: x["start"]):
            if cur and (len(cur) >= max_symbols or size + s["chars"] > max_chars):
                out.append((rel, cur))
                cur, size = [], 0
            cur.append(s)
            size += s["chars"]
        if cur:
            out.append((rel, cur))
    return out


def _test_file_for(rel: str, k: int, n: int, test_dir: str | None) -> str:
    p = PurePosixPath(rel)
    base = p.name.rsplit(".", 1)[0]
    suffix = f".factory-{k}" if n > 1 else ".factory"
    d = PurePosixPath(test_dir) if test_dir else p.parent / "__tests__"
    return str(d / f"{base}{suffix}.test.js")


def expand_template(tpl: dict, repo: Repo, task_id: str, snapshot: str | None) -> tuple[list[dict], dict]:
    """Template -> cards. Returns (cards, report{selected, skipped, capped})."""
    errs = schema_errors("template.v1", tpl)
    if errs:
        raise CardError(tpl.get("template_id", "?") if isinstance(tpl, dict) else "?", "SCHEMA", "; ".join(errs[:6]))
    kind = tpl["kind"]
    want_via = "find_undocumented" if kind == "doc_map" else "find_untested"
    if tpl["expand"]["via"] != want_via:
        raise CardError(tpl["template_id"], "SCHEMA", f"kind {kind} expands via {want_via}")
    if tpl["expand"]["repo"] != repo.name:
        raise CardError(tpl["template_id"], "REPO_MISMATCH", f"template repo {tpl['expand']['repo']} != {repo.name}")
    targets, skips = select_targets(tpl, repo)
    grp = tpl["expand"].get("group") or {}
    max_symbols = int(grp.get("max_symbols", 4 if kind == "doc_map" else 2))
    max_chars = int(grp.get("max_chars", 3600))
    groups = _groups(targets, max_symbols, max_chars)
    cap = int(tpl["expand"].get("cap", 100))
    capped = max(0, len(groups) - cap)
    groups = groups[:cap]
    base = tpl.get("card") or {}
    callers = int(base.get("callers_per_symbol", 4))
    per_file: dict[str, int] = {}
    for rel, _syms in groups:
        per_file[rel] = per_file.get(rel, 0) + 1
    seen_file: dict[str, int] = {}
    cards = []
    for i, (rel, syms) in enumerate(groups, 1):
        qn = [s["qname"] for s in syms]
        uid = f"{tpl['template_id']}-{i:03d}"
        listed = ", ".join(f"`{q}`" for q in qn)
        card: dict = {
            "card": 1, "unit_id": uid, "task_id": task_id, "snapshot": snapshot, "repo": repo.name,
            "group": uid, "risk": base.get("risk", "R0"), "kind": kind,
            "difficulty": base.get("difficulty", "easy"),
            "target": {"file": rel, "symbols": qn},
            "context": derive_context(repo, kind, rel, qn, callers),
            "deps": [],
            "retry": {"max_generations": min(int((base.get("retry") or {}).get("max_generations", 3)), 3),
                      **({"gpu_seconds": base["retry"]["gpu_seconds"]} if (base.get("retry") or {}).get("gpu_seconds") else {})},
            "priority": int(base.get("priority", 0)),
            "provenance": {"author": f"generator:{tpl['template_id']}", "template_id": tpl["template_id"],
                           "selection": [{"qname": s["qname"], "params": len(s["params"]), "loc": s["loc"],
                                          "branches": s["branchiness"]} for s in syms]},
        }
        if tpl.get("exemplar"):
            card["exemplar"] = {k: v for k, v in tpl["exemplar"].items() if k in ("file", "text")}
        if kind == "doc_map":
            card["target"]["anchor"] = {"mode": "before_symbol"}
            card["instruction"] = base.get("instruction") or (
                f"Write one JSDoc block for each of: {listed} (in {rel}). Follow DOC RULES. "
                "Types must hold under tsc --checkJs against the code and the call sites shown.")
            card["acceptance"] = base.get("acceptance") or [
                "Keys are exactly the listed symbols; each value is a single /** ... */ block.",
                "Every formal parameter has a typed @param with the same name and order; value-returning and async functions have @returns.",
                "Splicing the blocks above the declarations introduces 0 new tsc --checkJs errors in the repo.",
            ]
            card["provides"] = {"doc_symbols": qn, "export": False}
            card["constraints"] = {"output": "JSON object only", "signature": "locked (comments only)",
                                   "touch_only": "doc comments of the listed symbols"}
            card["output"] = "doc_map_json"
            card["verify"] = "docs_tsc"
            card["tests"] = {"files": [], "min_tests": 0}
        else:
            seen_file[rel] = seen_file.get(rel, 0) + 1
            test_file = _test_file_for(rel, seen_file[rel], per_file[rel], (base.get("tests") or {}).get("test_dir"))
            mutants = [m for s in syms for m in (s.get("mutants") or [])]
            card["instruction"] = base.get("instruction") or (
                f"Write a new jest test file {test_file} for {listed} (in {rel}). Characterize the current "
                "behaviour through the module's exports; cover the branches shown.")
            card["acceptance"] = base.get("acceptance") or [
                "All tests pass on the current code; no .only/.skip; no snapshots.",
                "Every test asserts a concrete value.",
                "At least 2 of 3 seeded faults per target make a test fail.",
            ]
            card["provides"] = {"test_file": test_file, "export": False}
            card["constraints"] = {"output": "one complete jest file", "touch_only": test_file}
            card["output"] = "new_file"
            card["verify"] = "tests"
            card["tests"] = {"files": [test_file], "min_tests": int((base.get("tests") or {}).get("min_tests", max(2, len(qn)))),
                             "kill_check": True, "mutants": mutants}
        cards.append(card)
    return cards, {"selected": len(targets), "units": len(cards), "capped": capped, "skipped": skips}
