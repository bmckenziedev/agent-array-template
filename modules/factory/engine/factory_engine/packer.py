"""Packer: card + repo snapshot -> minimal-context prompt, within the lane token budgets.

Context is given by reference in the card and cut out here from the acorn inventory (parse only;
js/inventory.js). The frontier never pastes code. Section order is chosen for prefix caching
(design §4): system, repo, rules, exemplar are identical for every unit of a repo/kind; file
context, target and task follow; retry feedback is appended last.

Budgets (design BUDGETS): prompt + max_tokens <= ctx - 256 per lane, prompt <= lane prompt_cap.
Over budget, context is dropped in a fixed order (helper bodies -> call sites -> module values ->
other files' signatures -> exemplar); if the target alone does not fit, the card is TOO_LARGE.
The packer, never the frontier, fills `budget` and `target.slice_sha256`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import config, jsbridge
from .tokens import Counter

NL = "\n"
PACKER_VERSION = "factory-v1"

SYSTEM = {
    "doc_map": (
        "You are a documentation engine. You get ONE work unit and return ONE JSON object.\n"
        "1. Output ONLY the JSON object, then the line CODE>>> . No prose, no markdown fences.\n"
        "2. Keys are exactly the requested symbols; each value is one JSDoc block string \"/**\\n * ...\\n */\".\n"
        "3. Document every parameter (@param {Type} name, in order) and the return value (@returns {Type}).\n"
        "4. Types must be correct for TypeScript's checkJs: they are verified by tsc against the code and its callers.\n"
        "5. Never change code; never write TODO or placeholders."
    ),
    "test_gen": (
        "You are a test-writing engine. You get ONE work unit and return ONE complete jest test file.\n"
        "1. Output the COMPLETE test file, then the line CODE>>> . Nothing else: no prose, no markdown fences.\n"
        "2. Never write \"...\", \"rest unchanged\", TODO, or placeholders. Every test is complete.\n"
        "3. Test only the TARGET functions, through the module's real exports. Never mock the module under test.\n"
        "4. Use only identifiers from CONTEXT, the target module's exports, JS/Node built-ins and jest globals.\n"
        "5. Match the repo: CommonJS require, 'use strict', 2-space indent, single quotes, semicolons.\n"
        "6. Every test asserts a concrete value (toBe/toEqual/toStrictEqual/toThrow(/message/)/toHaveBeenCalledWith...).\n"
        "   A test that only checks .not.toThrow(), toBeDefined(), toBeTruthy() or typeof is rejected."
    ),
}


class PackError(Exception):
    """A card that cannot be packed. `code` is the bounce class (TARGET_MISSING, TOO_LARGE, ...)."""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or {}


def chatml(system: str, user: str, prefill: str) -> str:
    return (f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n{prefill}")


def render_prompt(ep: dict, system: str, user: str) -> str:
    """Raw completion template, shared by packing and model I/O."""
    prefill = config.OPEN + "\n"
    tpl = ep.get("template", "chatml")
    if tpl == "chatml-nothink":
        prefill = "<think>\n\n</think>\n\n" + prefill
    if tpl == "none":
        return f"{system}\n\n{user}\n\n{prefill}"
    return chatml(system, user, prefill)


def prompt_count(counter: Counter, lanes: dict, system: str, user: str) -> int:
    """Conservative shared count across candidate completion templates."""
    return max(counter.count(render_prompt(l["endpoint"], system, user)) for l in lanes.values())


# --------------------------------------------------------------------------- repo access
class Repo:
    """Read-only view of one repo in a snapshot, plus its inventory (symbol spans)."""

    def __init__(self, name: str, root: Path, inventory: dict):
        self.name = name
        self.root = Path(root)
        self.inv = inventory
        self.files = {f["file"]: f for f in inventory.get("files", []) if "error" not in f}
        self.parse_errors = {f["file"]: f["error"] for f in inventory.get("files", []) if "error" in f}
        self._src: dict[str, str] = {}
        self._lines: list[tuple[str, int, str]] | None = None

    @classmethod
    def load(cls, name: str, root: Path, cache: Path | None = None, roots: list[str] | None = None) -> "Repo":
        if cache and cache.exists():
            inv = json.loads(cache.read_text(encoding="utf-8"))
        else:
            inv = jsbridge.inventory(root, roots=roots)
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(inv), encoding="utf-8")
        return cls(name, root, inv)

    def src(self, rel: str) -> str:
        if rel not in self._src:
            # raw bytes, not read_text(): universal-newline translation would turn CRLF into LF and shift every
            # acorn offset (computed by node over the raw text) by one per preceding line, so cuts, starts_line
            # and the prompt context of any CRLF repo would be garbage
            self._src[rel] = (self.root / rel).read_bytes().decode("utf-8")
        return self._src[rel]

    def cut(self, rel: str, a: int, b: int) -> str:
        """Slice by acorn offsets (UTF-16 code units; equal to str indices unless the file has astral chars)."""
        text = self.src(rel)
        if not self.files.get(rel, {}).get("astral"):
            return text[a:b]
        u = text.encode("utf-16-le")
        return u[2 * a:2 * b].decode("utf-16-le", errors="replace")

    def indent_at(self, rel: str, offset: int) -> str:
        seg = self.cut(rel, max(0, offset - 400), offset)
        seg = seg[seg.rfind(NL) + 1:]
        return seg if seg.strip() == "" else ""

    def starts_line(self, rel: str, offset: int) -> bool:
        """Only whitespace precedes `offset` on its line, so a doc block can be spliced above it."""
        seg = self.cut(rel, max(0, offset - 400), offset)
        return seg[seg.rfind(NL) + 1:].strip() == ""

    def sym(self, rel: str, qname: str) -> dict | None:
        for s in self.files.get(rel, {}).get("symbols", []):
            if s["qname"] == qname and s.get("occurrence", 1) == 1:
                return s
        return None

    def binding(self, rel: str, name: str) -> dict | None:
        for b in self.files.get(rel, {}).get("bindings", []):
            if b["name"] == name:
                return b
        return None

    def sym_text(self, rel: str, s: dict, detail: str) -> str:
        start = s["jsdoc"]["start"] if s.get("jsdoc") else s["start"]
        indent = self.indent_at(rel, start)
        if detail == "full":
            return (indent + self.cut(rel, start, s["end"])).replace("\r", "")
        doc = (indent + self.cut(rel, s["jsdoc"]["start"], s["jsdoc"]["end"]) + NL) if s.get("jsdoc") else ""
        return (doc + indent + s["signature"]).replace("\r", "")

    def binding_text(self, rel: str, b: dict, detail: str) -> str:
        text = self.cut(rel, b["start"], b["end"]).replace("\r", "")
        limit = 3500 if detail == "full" else 1400
        if detail in ("full", "head") and len(text) <= limit:
            return text
        first = text.split(NL, 1)[0]
        if detail in ("full", "head"):
            cut = text[:limit]
            cut = cut[:cut.rfind(NL)] if NL in cut else cut
            return cut + "\n  // … (truncated by the packer)"
        return first + (" … }" if first.rstrip().endswith("{") else "")

    def imports_text(self, rel: str) -> str:
        f = self.files.get(rel, {})
        return NL.join(self.cut(rel, r["start"], r["end"]) for r in f.get("requireStmts", [])).replace("\r", "")

    def class_outline(self, rel: str, cls: str, full: set[str], skip: set[str]) -> str:
        lines = [f"class {cls} {{"]
        for s in self.files[rel]["symbols"]:
            if s["className"] != cls or s["qname"] in skip:
                continue
            if s["kind"] == "constructor" or s["qname"] in full:
                lines.append(self.sym_text(rel, s, "full"))
            else:
                lines.append(self.indent_at(rel, s["start"]) + s["signature"])
        lines.append("}")
        return NL.join(lines)

    def slice_sha(self, rel: str, qnames: list[str]) -> str:
        """Matches js/lib/docsplice.js sliceSha: sha256 over per-symbol sha256s joined by \\n."""
        parts = []
        for q in qnames:
            s = self.sym(rel, q)
            if s is None:
                raise PackError("TARGET_MISSING", f"{q} not found in {rel}")
            parts.append(s["sha256"])
        return hashlib.sha256(NL.join(parts).encode("utf-8")).hexdigest()

    def resolve_require(self, from_rel: str, spec: str) -> str | None:
        if not spec.startswith("."):
            return None
        base = PurePosixPath(from_rel).parent
        parts: list[str] = []
        for p in (base / spec).parts:
            if p == "..":
                if parts:
                    parts.pop()
            elif p != ".":
                parts.append(p)
        cand = "/".join(parts)
        for c in (cand, cand + ".js", cand + ".cjs", cand + "/index.js"):
            if c in self.files:
                return c
        return None

    def repo_lines(self) -> list[tuple[str, int, str]]:
        if self._lines is None:
            rows = []
            for rel in sorted(self.files):
                for i, line in enumerate(self.src(rel).replace("\r", "").split(NL), 1):
                    rows.append((rel, i, line))
            self._lines = rows
        return self._lines

    def call_sites(self, qnames: list[str], per_symbol: int) -> list[str]:
        """`file:line: code` for calls of each symbol (fn(...), .method(...), new Class(...))."""
        out = []
        for q in qnames:
            parts = q.split(".")
            if len(parts) == 2 and parts[1] == "constructor":
                pat = re.compile(rf"\bnew\s+{re.escape(parts[0])}\s*\(")
            elif len(parts) == 2:
                pat = re.compile(rf"\.{re.escape(parts[1].split(':')[-1])}\s*\(")
            else:
                # plain calls and calls through the module object (money.toCents(...))
                pat = re.compile(rf"(?<![\w$]){re.escape(q)}\s*\(")
            name = parts[-1].split(":")[-1]
            defn = re.compile(rf"^\s*(?:async\s+)?(?:static\s+)?(?:function\s*\*?\s*)?{re.escape(name)}\s*\([^)]*\)\s*\{{")
            hits = []
            for rel, i, line in self.repo_lines():
                s = line.strip()
                if pat.search(line) and not defn.search(line) and not s.startswith(("*", "//")):
                    hits.append(f"{rel}:{i}: {s[:160]}")
            out += hits[:per_symbol]
            if len(hits) > per_symbol:
                out.append(f"... {len(hits) - per_symbol} more call(s) of {q}")
        return out


# --------------------------------------------------------------------------- context derivation
def derive_context(repo: Repo, kind: str, rel: str, symbols: list[str], callers: int = 4) -> list[dict]:
    """Deterministic context references for a target set (used by the generator)."""
    f = repo.files[rel]
    ctx: list[dict] = [{"file": rel, "kind": "imports"}]
    syms = [repo.sym(rel, q) for q in symbols]
    classes = sorted({s["className"] for s in syms if s and s.get("className")})
    for cls in classes:
        helpers = set()
        for s in syms:
            if s and s.get("className") == cls:
                helpers.update(f"{cls}.{m}" for m in s.get("thisCalls", []))
        known = {s2["qname"] for s2 in f["symbols"]}
        full = sorted(h for h in helpers if h in known and h not in symbols)[:3]
        ctx.append({"file": rel, "kind": "class_outline", "class": cls, "full": full})
    used = sorted({u for s in syms if s for u in s.get("usedModule", [])})
    vals = [u for u in used if (b := repo.binding(rel, u)) and b["kind"] != "class"
            and not any(x["qname"] == u for x in f["symbols"])]
    if vals:
        ctx.append({"file": rel, "kind": "bindings", "symbols": vals[:12], "detail": "full"})
    same_file_fns = [u for u in used if any(x["qname"] == u for x in f["symbols"]) and u not in symbols]
    if same_file_fns:
        ctx.append({"file": rel, "kind": "symbols", "symbols": same_file_fns[:8], "detail": "signature"})
    used_req = sorted({u for s in syms if s for u in s.get("usedRequire", [])})
    for r in f.get("requires", []):
        target = repo.resolve_require(rel, r["spec"])
        if not target:
            continue
        names = [n["imported"] for n in r.get("names", []) if n.get("local") in used_req and n.get("imported")]
        names = [n for n in names if repo.sym(target, n)]
        if names:
            ctx.append({"file": target, "kind": "symbols", "symbols": names[:8], "detail": "signature"})
    if kind == "doc_map" and callers > 0:
        ctx.append({"file": rel, "kind": "callers", "symbols": list(symbols), "max": callers})
    return ctx


# --------------------------------------------------------------------------- rendering
@dataclass
class Packed:
    system: str
    user: str
    prompt_tokens: int
    max_tokens: int
    dropped: list[str] = field(default_factory=list)
    lane_fit: list[str] = field(default_factory=list)


def _context_text(repo: Repo, card: dict, ctx_items: list[dict]) -> str:
    tgt = card["target"]
    parts = []
    for item in ctx_items:
        f = item["file"]
        if f not in repo.files:
            continue
        k = item.get("kind")
        if k == "imports":
            t = repo.imports_text(f)
            if t:
                parts.append(f"### {f} - imports\n{t}")
        elif k == "class_outline":
            parts.append(f"### {f} - class {item['class']} (constructor and used methods in full, other bodies elided)\n"
                         + repo.class_outline(f, item["class"], set(item.get("full", [])), set(tgt["symbols"])))
        elif k == "bindings":
            texts = [repo.binding_text(f, b, item.get("detail", "full"))
                     for n in item.get("symbols", []) if (b := repo.binding(f, n))]
            if texts:
                parts.append(f"### {f} - referenced module-level values\n" + "\n\n".join(texts))
        elif k == "symbols":
            texts = [repo.sym_text(f, s, item.get("detail", "signature"))
                     for q in item.get("symbols", []) if (s := repo.sym(f, q))]
            if texts:
                parts.append(f"### {f} - referenced symbols\n" + "\n\n".join(texts))
        elif k == "callers":
            lines = repo.call_sites(item.get("symbols", []), int(item.get("max", 4)))
            if lines:
                parts.append("### call sites in this repo (tsc checks the documented types against these)\n" + NL.join(lines))
    return ("## CONTEXT (read-only)\n" + "\n\n".join(parts)) if parts else ""


def render_parts(card: dict, repo: Repo, profile: dict, exemplar: str | None,
                 ctx_items: list[dict] | None = None, feedback: str | None = None) -> tuple[str, str]:
    kind = card["kind"]
    tgt = card["target"]
    rel = tgt["file"]
    secs = [f"## REPO\n{card['repo']}: {profile.get('language', 'JavaScript')}. Style: {profile.get('style', '')}".rstrip()]
    note_src = ((profile.get("jest") or {}).get("prompt_notes") if kind == "test_gen" else None) or         profile.get("prompt_notes", [])
    notes = NL.join(f"- {n}" for n in note_src)
    if notes:
        secs.append(f"## {'DOC RULES' if kind == 'doc_map' else 'TEST PROFILE (applied by the runner)'}\n{notes}")
    if exemplar:
        secs.append(f"## EXEMPLAR ({'output format to follow' if kind == 'doc_map' else 'style to follow'})\n{exemplar}")
    ctx = _context_text(repo, card, card.get("context", []) if ctx_items is None else ctx_items)
    if ctx:
        secs.append(ctx)
    tparts = []
    for q in tgt["symbols"]:
        s = repo.sym(rel, q)
        if s is None:
            raise PackError("TARGET_MISSING", f"{q} not found in {rel}")
        tparts.append(f"### `{q}` (lines {s['line']}-{s['endLine']})\n" + repo.sym_text(rel, s, "full"))
    label = "SYMBOLS TO DOCUMENT" if kind == "doc_map" else "TARGET functions"
    secs.append(f"## {label} in {rel}\n" + "\n\n".join(tparts))
    acc = NL.join(f"- {a}" for a in card.get("acceptance", []))
    cons = "; ".join(f"{k}: {v}" for k, v in (card.get("constraints") or {}).items())
    task = f"## TASK\n{card['instruction']}"
    if acc:
        task += f"\nAcceptance:\n{acc}"
    if kind == "test_gen":
        task += f"\nWrite the file {card['provides']['test_file']}; it must contain at least {card['tests']['min_tests']} tests."
    if cons:
        task += f"\nConstraints: {cons}"
    secs.append(task)
    user = "\n\n".join(secs)
    if feedback:
        user += "\n\n" + feedback
    return SYSTEM[kind], user


def feedback_block(stage: str, message: str, kind: str, counter: Counter,
                   budget_tokens: int = config.FEEDBACK_TOKENS) -> str:
    what = "JSON doc map" if kind == "doc_map" else "test file"
    msg = (message or "").strip()
    while counter.count(msg) > budget_tokens and len(msg) > 200:
        msg = msg[: int(len(msg) * 0.8)] + " …"
    return f"## PREVIOUS ATTEMPT FAILED ({stage})\n{msg}\nReturn the complete {what} again, fixed."


def max_tokens_for(card: dict) -> int:
    n = len(card["target"]["symbols"])
    if card["kind"] == "doc_map":
        return min(config.MAX_TOKENS["doc_map"], config.DOC_TOKENS_BASE + config.DOC_TOKENS_PER_SYMBOL * n)
    return config.MAX_TOKENS.get(card["kind"], 2000)


def lanes_for(card: dict, lanes_cfg: dict) -> list[str]:
    ladder = lanes_cfg["routing"][card["kind"]][card["difficulty"]]
    out: list[str] = []
    for rung in ladder:
        for ln in [rung["lane"], *rung.get("steal_to", [])]:
            if ln not in out:
                out.append(ln)
    return out


def fits(lane: dict, prompt_tokens: int, max_tokens: int) -> bool:
    return prompt_tokens <= int(lane["prompt_cap"]) and prompt_tokens + max_tokens <= int(lane["ctx"]) - config.CTX_MARGIN


def _drop_steps(ctx_items: list[dict]):
    """Yield (label, new_ctx, drop_exemplar) in the fixed drop order."""
    cur = copy.deepcopy(ctx_items)
    for it in cur:
        if it["kind"] == "class_outline" and it.get("full"):
            it["full"] = []
    yield "class_outline: helper bodies -> signatures", copy.deepcopy(cur), False
    for it in cur:
        if it["kind"] == "callers" and it.get("max", 0) > 1:
            it["max"] = 1
    yield "call sites -> 1 per symbol", copy.deepcopy(cur), False
    cur = [it for it in cur if it["kind"] != "callers"]
    yield "call sites dropped", copy.deepcopy(cur), False
    for it in cur:
        if it["kind"] == "bindings":
            it["detail"] = "head"
    yield "module values -> head", copy.deepcopy(cur), False
    for it in cur:
        if it["kind"] == "bindings":
            it["detail"] = "signature"
    yield "module values -> one line", copy.deepcopy(cur), False
    tfile = None
    for it in cur:
        if it["kind"] == "imports":
            tfile = it["file"]
    cur = [it for it in cur if not (it["kind"] == "symbols" and it["file"] != tfile)]
    yield "other files' signatures dropped", copy.deepcopy(cur), False
    yield "exemplar dropped", copy.deepcopy(cur), True


def pack(card: dict, repo: Repo, profile: dict, exemplar: str | None, counter: Counter,
         lanes_cfg: dict) -> tuple[dict, Packed]:
    """Fill budget + slice_sha256 into a copy of the card and return it with the rendered parts."""
    card = copy.deepcopy(card)
    rel = card["target"]["file"]
    if rel not in repo.files:
        why = repo.parse_errors.get(rel)
        if why:
            raise PackError("TARGET_MISSING", f"{rel} does not parse: {why}")
        raise PackError("TARGET_MISSING", f"{rel} is not a source file in {repo.name} (inventory roots)")
    for q in card["target"]["symbols"]:
        s = repo.sym(rel, q)
        if s is None:
            raise PackError("TARGET_MISSING", f"{q} not found in {rel}")
        if card["kind"] == "doc_map" and s.get("jsdoc"):
            raise PackError("ALREADY_DOCUMENTED", f"{q} in {rel} already has a doc block")
        if card["kind"] == "doc_map" and not repo.starts_line(rel, s["start"]):
            # the gate could never splice it; every generation would be wasted as a model failure
            raise PackError("NOT_SPLICEABLE", f"{q} in {rel} does not start its line (code before it on the same line)")
        dup = [x for x in repo.files[rel]["symbols"] if x["qname"] == q]
        if len(dup) > 1:
            raise PackError("AMBIGUOUS_TARGET", f"{q} is declared {len(dup)} times in {rel}")
    card["target"]["slice_sha256"] = repo.slice_sha(rel, card["target"]["symbols"])
    if card["kind"] == "test_gen":
        # Like budget and slice_sha256, the mutants come from the packer, never from the card: a card
        # with no mutants (or hand-made ones) would make the mutant gate vacuous or trivially killed.
        muts: list[dict] = []
        for q in card["target"]["symbols"]:
            sm = list(repo.sym(rel, q).get("mutants") or [])
            if len(sm) < 3:
                raise PackError("TOO_FEW_MUTANTS", f"{q} in {rel} has {len(sm)} mutation site(s); the mutant gate "
                                "needs 3 per target (>= 2 killed)")
            muts += sm
        card.setdefault("tests", {})["mutants"] = muts
        card["tests"]["kill_check"] = True
    mt = max_tokens_for(card)
    lane_names = lanes_for(card, lanes_cfg)
    lanes = {n: lanes_cfg["lanes"][n] for n in lane_names}
    pack_cap = min(int(l["prompt_cap"]) for l in lanes.values())
    biggest = max(int(l["prompt_cap"]) for l in lanes.values())

    base_ctx = card.get("context", [])
    attempts = [("", copy.deepcopy(base_ctx), False), *_drop_steps(base_ctx)]
    dropped: list[str] = []
    best = None
    for label, ctx_items, drop_ex in attempts:
        if label:
            dropped.append(label)
        system, user = render_parts(card, repo, profile, None if drop_ex else exemplar, ctx_items)
        n = prompt_count(counter, lanes, system, user)
        best = (system, user, n, ctx_items, drop_ex)
        if n <= pack_cap and n + mt <= min(int(l["ctx"]) for l in lanes.values()) - config.CTX_MARGIN:
            break
    else:
        # nothing fits the smallest lane: accept the largest lane that still fits
        system, user, n, ctx_items, drop_ex = best
        if n > biggest:
            raise PackError("TOO_LARGE", f"prompt is {n} tokens after every drop step; the largest lane cap is "
                            f"{biggest}. Split the unit or let the frontier own it.",
                            {"prompt_tokens": n, "cap": biggest, "max_tokens": mt})
    system, user, n, ctx_items, drop_ex = best
    fit = [ln for ln, l in lanes.items() if fits(l, n, mt)]
    if not fit:
        raise PackError("TOO_LARGE", f"prompt {n} + max_tokens {mt} fits no lane of this card's ladder",
                        {"prompt_tokens": n, "max_tokens": mt})
    card["context"] = ctx_items
    if drop_ex:
        card.pop("exemplar", None)
    card["budget"] = {"prompt_tokens": n, "max_tokens": mt, "pack_cap": pack_cap, "lane_fit": fit,
                      "counted_by": counter.counted_by, "dropped": dropped}
    prov = card.setdefault("provenance", {})
    prov["packer"] = PACKER_VERSION
    return card, Packed(system, user, n, mt, dropped, fit)


def exemplar_text(card: dict, profile: dict, profile_dir: Path | None = None) -> str | None:
    pex = (profile.get("jest") or {}).get("exemplar") if card.get("kind") == "test_gen" else profile.get("exemplar")
    ex = card.get("exemplar") or pex or {}
    if ex.get("text"):
        return ex["text"]
    if ex.get("file") and profile_dir:
        p = (profile_dir / ex["file"]).resolve()
        if p.is_file() and profile_dir.resolve() in p.parents:
            return p.read_text(encoding="utf-8")
    return None
