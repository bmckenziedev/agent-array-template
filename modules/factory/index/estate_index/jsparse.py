"""Parse-only extraction of JS/TS facts with tree-sitter. Nothing is evaluated or required.

`parse_file(src_bytes, ext)` returns a FileFacts with:
- symbols: top-level functions, classes (+ methods / function-valued fields), top-level
  const objects (+ their function members), other top-level consts, TS interfaces/types/
  enums, and functions defined inline in `module.exports = {...}` / `exports.x = ...`;
  each with byte span, lines, signature, params, JSDoc (presence + @param/@returns types),
  branchiness, returns_value and its own IO tags;
- imports: require(...) (destructured, namespace, `require(x).member`, lazy), ESM import,
  `export ... from`, dynamic import(), jest.mock / jest.requireActual;
- exports: module.exports = {...} (shorthand, pairs, inline functions, `...require()`
  spreads, `key: require()`), module.exports = X / new X() / function / class,
  exports.x = ..., module.exports.x = ..., ESM export forms;
- uses: identifier and member references that may point at a local symbol or an import
  binding (call / new / ref), `this.x` uses, and the per-file count of `.name(` calls.
Cross-file resolution happens in build.py.

Approximations (documented, deliberate): no scope analysis, so a parameter that shadows an
import binding counts as a use of the binding; instance-method calls (`obj.m()`) are matched
by name in build.py, not by type.
"""
from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field

import tree_sitter as ts
import tree_sitter_javascript as tsjs

try:  # optional: TS/TSX (typed modules)
    import tree_sitter_typescript as tsts
except ImportError:  # pragma: no cover
    tsts = None

EXT_LANG = {".js": "javascript", ".cjs": "javascript", ".mjs": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}

# IO that a secret-free, network-free tester cannot satisfy (critique: skip or profile these targets).
IO_MODULES = {"child_process", "fs", "fs/promises", "net", "http", "https", "http2", "dgram", "tls",
              "worker_threads", "cluster", "pg", "ws", "dns", "readline"}
# Same tags as factory/engine/js/inventory.js (own IO / nondeterminism of a symbol).
IO_GLOBAL_CALLS = {"fetch": "fetch", "setInterval": "setInterval", "setTimeout": "setTimeout",
                   "setImmediate": "setImmediate"}
IO_MEMBERS = {("process", "exit"): "process.exit", ("process", "env"): "process.env", ("Date", "now"): "clock",
              ("Math", "random"): "Math.random"}
IO_GLOBAL_OBJECTS = {"process", "Date", "Math"}
JEST_MOCK_PROPS = {"mock", "doMock", "requireActual", "requireMock", "unmock", "createMockFromModule"}
# Receivers that are never an instance of an estate class: `console.log(` must not count as a call of
# Logger.log, nor `JSON.parse(` of Parser.parse (instance-method matching in build.py).
BUILTIN_RECEIVERS = {"console", "JSON", "Math", "Object", "Array", "Promise", "Reflect", "Number", "String",
                     "Boolean", "Symbol", "Date", "process", "Buffer", "Intl", "globalThis", "BigInt", "Atomics",
                     "Error", "require", "module", "exports", "jest", "expect"}

_Q_DECISION = """
[(if_statement) (ternary_expression) (for_statement) (for_in_statement) (while_statement)
 (do_statement) (catch_clause) (optional_chain)] @d
(switch_case value: (_)) @d
(binary_expression operator: ["&&" "||" "??"]) @d
(augmented_assignment_expression operator: ["&&=" "||=" "??="]) @d
"""
_Q_CALL = """
(call_expression function: (_) @fn) @call
(new_expression constructor: (_) @fn) @call
"""
_Q_IDENT = "(identifier) @id"
_Q_MEMBER = "(member_expression object: [(identifier) (this)] @obj property: (property_identifier) @prop) @m"
_Q_RETURN = "(return_statement (_)) @r"
_Q_ERROR = "(ERROR) @e"

_FUNC_TYPES = {"function_expression", "function", "arrow_function", "generator_function"}
_FUNC_DECL = {"function_declaration", "generator_function_declaration"}
_CLASS_TYPES = {"class", "class_declaration", "abstract_class_declaration"}
_DECL_PARENTS = {"function_declaration", "generator_function_declaration", "function_expression", "function",
                 "generator_function", "class_declaration", "class", "abstract_class_declaration",
                 "method_definition"}
_PATTERN_PARENTS = {"formal_parameters", "assignment_pattern", "rest_pattern", "pair_pattern", "object_pattern",
                    "array_pattern", "object_assignment_pattern", "required_parameter", "optional_parameter",
                    "import_specifier", "import_clause", "namespace_import", "export_specifier", "catch_clause",
                    "labeled_statement", "break_statement", "continue_statement"}

_LANG_CACHE: dict[str, "_Lang"] = {}


class _Lang:
    def __init__(self, name: str):
        if name == "javascript":
            lang = ts.Language(tsjs.language())
        elif tsts is None:
            raise ValueError("tree_sitter_typescript is not installed")
        elif name == "typescript":
            lang = ts.Language(tsts.language_typescript())
        else:
            lang = ts.Language(tsts.language_tsx())
        self.name = name
        self.language = lang
        self.parser = ts.Parser(lang)
        self.q_decision = ts.Query(lang, _Q_DECISION)
        self.q_call = ts.Query(lang, _Q_CALL)
        self.q_ident = ts.Query(lang, _Q_IDENT)
        self.q_member = ts.Query(lang, _Q_MEMBER)
        self.q_return = ts.Query(lang, _Q_RETURN)
        self.q_error = ts.Query(lang, _Q_ERROR)


def lang_for(ext: str) -> "_Lang | None":
    name = EXT_LANG.get(ext.lower())
    if name is None or (name != "javascript" and tsts is None):
        return None
    if name not in _LANG_CACHE:
        _LANG_CACHE[name] = _Lang(name)
    return _LANG_CACHE[name]


def parser_versions() -> dict:
    from importlib import metadata
    out = {}
    for dist in ("tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None
    return out


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #
@dataclass
class Sym:
    name: str
    qualname: str
    kind: str               # function|class|method|constructor|getter|setter|object|value|interface|type|enum
    parent: int | None
    start: int
    end: int
    line: int
    end_line: int
    signature: str
    params: list[str]
    is_async: bool = False
    is_generator: bool = False
    is_static: bool = False
    private: bool = False
    inline_export: bool = False
    jsdoc: dict | None = None
    branchiness: int = 0
    returns_value: bool = False
    io: set[str] = field(default_factory=set)


@dataclass
class Imp:
    spec: str
    kind: str               # require|import|dynamic|jest_mock|reexport
    line: int
    start: int              # declaration span (excluded from reference scanning)
    end: int
    local: str | None = None            # namespace binding: const x = require(spec) / import * as x
    names: list[tuple[str, str]] = field(default_factory=list)   # (imported, local)
    member: str | None = None           # const x = require(spec).member
    lazy: bool = False                  # inside a function body


@dataclass
class Exp:
    name: str
    kind: str               # local|inline|reexport|reexport_all|module|instance|value|spread_local
    line: int
    local: str | None = None
    sym: int | None = None
    spec: str | None = None
    imported: str | None = None


@dataclass
class Use:
    pos: int
    line: int
    name: str               # identifier, or the object of a member use ('this' for this.x)
    member: str | None
    kind: str               # call|new|ref


@dataclass
class FileFacts:
    lang: str
    lines: int
    errors: int
    symbols: list[Sym] = field(default_factory=list)
    imports: list[Imp] = field(default_factory=list)
    exports: list[Exp] = field(default_factory=list)
    uses: list[Use] = field(default_factory=list)
    member_calls: Counter = field(default_factory=Counter)        # `.name(` calls (builtin receivers excluded)
    member_recv: Counter = field(default_factory=Counter)         # (name, receiver identifier) -> calls
    io_uses: list[tuple[int, str]] = field(default_factory=list)   # (pos, tag)
    use_owner: list[int | None] = field(default_factory=list)      # innermost symbol per use


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_WS = re.compile(r"\s+")
_JSDOC_TAG = re.compile(
    r"@(param|arg|argument|returns?|type|typedef|throws|template|property|prop)\b[ \t]*"
    r"(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})?[ \t]*(\[[^\]]*\]|[\w$.\[\]]+)?")


def _t(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _str_value(node, src: bytes) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        return _t(src, node)[1:-1]
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children):
        return _t(src, node)[1:-1]
    return None


def _first_arg(call):
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for c in args.named_children:
        if c.type != "comment":
            return c
    return None


def _is_module_exports(node, src: bytes) -> bool:
    return (node is not None and node.type == "member_expression"
            and _t(src, node).replace(" ", "") == "module.exports")


def _key_text(node, src: bytes) -> str | None:
    if node is None:
        return None
    if node.type in ("property_identifier", "identifier", "private_property_identifier", "number"):
        return _t(src, node)
    if node.type == "string":
        return _t(src, node)[1:-1]
    return None   # computed keys


def parse_jsdoc(text: str) -> dict:
    body = text[3:-2] if text.startswith("/**") else text
    lines = [re.sub(r"^\s*\*\s?", "", ln) for ln in body.splitlines()]
    flat = "\n".join(lines)
    params, returns, typedef, typ = [], None, False, None
    for m in _JSDOC_TAG.finditer(flat):
        tag, ty, name = m.group(1), m.group(2), m.group(3)
        ty = ty[1:-1].strip() if ty else None
        if tag in ("param", "arg", "argument"):
            nm = (name or "").strip("[]").split("=")[0]
            params.append({"name": nm, "type": ty})
        elif tag in ("returns", "return"):
            returns = ty or "?"
        elif tag == "typedef":
            typedef = True
        elif tag == "type":
            typ = ty
    summary = any(ln.strip() and not ln.strip().startswith("@") for ln in lines)
    return {"params": params, "returns": returns, "typedef": typedef, "type": typ, "summary": summary}


def _jsdoc_before(node, src: bytes) -> dict | None:
    if node is None:
        return None
    prev = node.prev_sibling
    while prev is not None and prev.type == ",":
        prev = prev.prev_sibling
    if prev is None or prev.type != "comment":
        return None
    text = _t(src, prev)
    if not text.startswith("/**") or src[prev.end_byte:node.start_byte].strip(b" \t\r\n,"):
        return None
    d = parse_jsdoc(text)
    d["start"], d["end"] = prev.start_byte, prev.end_byte
    return d


def _params(fn, src: bytes) -> list[str]:
    p = fn.child_by_field_name("parameters")
    if p is None:
        single = fn.child_by_field_name("parameter")
        return [_t(src, single)] if single is not None else []
    return [_WS.sub(" ", _t(src, c))[:80] for c in p.named_children if c.type != "comment"]


def _sig(prefix: str, name: str, fn, src: bytes) -> str:
    p = fn.child_by_field_name("parameters")
    if p is not None:
        ptxt = _t(src, p)
    else:
        single = fn.child_by_field_name("parameter")
        ptxt = f"({_t(src, single)})" if single is not None else "()"
    rt = fn.child_by_field_name("return_type")
    rtxt = _t(src, rt) if rt is not None else ""
    s = _WS.sub(" ", f"{prefix}{name}{ptxt}{rtxt}").strip()
    return s if len(s) <= 240 else s[:237] + "..."


_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def _param_names(params: list[str]) -> set[str]:
    out: set[str] = set()
    for p in params:
        head = p.lstrip(".").split("=", 1)[0].strip()
        if head.startswith(("{", "[")):
            out.update(_IDENT.findall(head))
        else:
            m = _IDENT.match(head)
            if m:
                out.add(m.group(0))
    return out


def _has_kw(node, kw: str) -> bool:
    return any(c.type == kw for c in node.children)


def _class_sig(node, name: str, src: bytes) -> str:
    her = next((c for c in node.children if c.type == "class_heritage"), None)
    return _WS.sub(" ", f"class {name}" + (f" {_t(src, her)}" if her is not None else ""))[:240]


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
class _Extractor:
    def __init__(self, src: bytes, lang: _Lang):
        self.src = src
        self.lang = lang
        self.syms: list[Sym] = []
        self.imps: list[Imp] = []
        self.exps: list[Exp] = []
        self.top_names: dict[str, int] = {}

    # -- symbols -------------------------------------------------------------
    def add(self, sym: Sym) -> int:
        self.syms.append(sym)
        idx = len(self.syms) - 1
        if sym.parent is None and sym.name not in self.top_names:
            self.top_names[sym.name] = idx
        return idx

    def _span_sym(self, span_node, **kw) -> Sym:
        return Sym(start=span_node.start_byte, end=span_node.end_byte, line=span_node.start_point.row + 1,
                   end_line=span_node.end_point.row + 1, **kw)

    def func_sym(self, name: str, fn, span_node, doc_node, parent: int | None = None, kind: str = "function",
                 qual: str | None = None, prefix: str = "", static: bool = False, inline: bool = False) -> int:
        src = self.src
        is_async = _has_kw(fn, "async")
        is_gen = fn.type in ("generator_function", "generator_function_declaration") or _has_kw(fn, "*")
        pre = ("static " if static else "") + ("async " if is_async else "") + prefix
        s = self._span_sym(span_node, name=name, qualname=qual or name, kind=kind, parent=parent,
                           signature=_sig(pre, qual or name, fn, src), params=_params(fn, src),
                           is_async=is_async, is_generator=is_gen, is_static=static,
                           private=name.startswith(("_", "#")), inline_export=inline,
                           jsdoc=_jsdoc_before(doc_node, src))
        if fn.type == "arrow_function":
            body = fn.child_by_field_name("body")
            if body is not None and body.type != "statement_block":
                s.returns_value = True
        return self.add(s)

    def class_sym(self, name: str, cls, span_node, doc_node, inline: bool = False) -> int:
        src = self.src
        ci = self.add(self._span_sym(span_node, name=name, qualname=name, kind="class", parent=None,
                                     signature=_class_sig(cls, name, src), params=[], private=name.startswith("_"),
                                     inline_export=inline, jsdoc=_jsdoc_before(doc_node, src)))
        body = cls.child_by_field_name("body")
        if body is None:
            return ci
        for m in body.named_children:
            if m.type == "method_definition":
                mname = _key_text(m.child_by_field_name("name"), src)
                if mname is None:
                    continue
                static = _has_kw(m, "static")
                kind, prefix = "method", ""
                if mname == "constructor":
                    kind = "constructor"
                elif _has_kw(m, "get"):
                    kind, prefix = "getter", "get "
                elif _has_kw(m, "set"):
                    kind, prefix = "setter", "set "
                self.func_sym(mname, m, m, m, parent=ci, kind=kind, qual=f"{name}.{mname}", prefix=prefix,
                              static=static)
            elif m.type in ("field_definition", "public_field_definition"):
                key = m.child_by_field_name("property") or m.child_by_field_name("name")
                val = m.child_by_field_name("value")
                mname = _key_text(key, src)
                if mname and val is not None and val.type in _FUNC_TYPES:
                    self.func_sym(mname, val, m, m, parent=ci, kind="method", qual=f"{name}.{mname}",
                                  static=_has_kw(m, "static"))
        return ci

    def object_sym(self, name: str, obj, span_node, doc_node) -> int:
        src = self.src
        oi = self.add(self._span_sym(span_node, name=name, qualname=name, kind="object", parent=None,
                                     signature=f"const {name} = {{...}}", params=[], private=name.startswith("_"),
                                     jsdoc=_jsdoc_before(doc_node, src)))
        for m in obj.named_children:
            if m.type == "method_definition":
                mname = _key_text(m.child_by_field_name("name"), src)
                if mname:
                    self.func_sym(mname, m, m, m, parent=oi, kind="method", qual=f"{name}.{mname}")
            elif m.type == "pair":
                mname = _key_text(m.child_by_field_name("key"), src)
                val = m.child_by_field_name("value")
                if mname and val is not None and val.type in _FUNC_TYPES:
                    self.func_sym(mname, val, m, m, parent=oi, kind="method", qual=f"{name}.{mname}")
        return oi

    def value_sym(self, name: str, decl, span_node, doc_node, kind_kw: str) -> int:
        src = self.src
        tnode = decl.child_by_field_name("type")
        sig = f"{kind_kw} {name}" + (_t(src, tnode) if tnode is not None else "")
        return self.add(self._span_sym(span_node, name=name, qualname=name, kind="value", parent=None,
                                       signature=_WS.sub(" ", sig)[:240], params=[], private=name.startswith("_"),
                                       jsdoc=_jsdoc_before(doc_node, src)))

    # -- top level -------------------------------------------------------------
    def top(self, root) -> None:
        for stmt in root.named_children:
            self.statement(stmt, stmt, exported=None)

    def _export_decl(self, exported: str | None, name: str, idx: int, line: int) -> None:
        if exported:
            self.exps.append(Exp(name if exported == "*" else exported, "inline", line, sym=idx))

    def statement(self, stmt, doc_node, exported: str | None) -> None:
        src = self.src
        t = stmt.type
        line = stmt.start_point.row + 1
        if t == "export_statement":
            self.esm_export(stmt)
        elif t in _FUNC_DECL:
            nm = stmt.child_by_field_name("name")
            if nm is not None:
                self._export_decl(exported, _t(src, nm), self.func_sym(_t(src, nm), stmt, stmt, doc_node), line)
        elif t in ("class_declaration", "abstract_class_declaration"):
            nm = stmt.child_by_field_name("name")
            if nm is not None:
                self._export_decl(exported, _t(src, nm), self.class_sym(_t(src, nm), stmt, stmt, doc_node), line)
        elif t in ("lexical_declaration", "variable_declaration"):
            kind_kw = _t(src, stmt.children[0]) if stmt.children else "const"
            decls = [d for d in stmt.named_children if d.type == "variable_declarator"]
            for d in decls:
                nm = d.child_by_field_name("name")
                val = d.child_by_field_name("value")
                if nm is None or nm.type != "identifier":
                    continue  # destructuring: handled by the require scan
                name = _t(src, nm)
                span = stmt if len(decls) == 1 else d
                if val is not None and self._is_require_like(val):
                    continue  # an import binding, not a symbol
                if val is not None and val.type in _FUNC_TYPES:
                    i = self.func_sym(name, val, span, doc_node)
                elif val is not None and val.type in _CLASS_TYPES:
                    i = self.class_sym(name, val, span, doc_node)
                elif val is not None and val.type == "object" and any(
                        c.type in ("method_definition", "pair") and (
                            c.type == "method_definition" or (c.child_by_field_name("value") is not None and
                                                              c.child_by_field_name("value").type in _FUNC_TYPES))
                        for c in val.named_children):
                    i = self.object_sym(name, val, span, doc_node)
                else:
                    i = self.value_sym(name, d, span, doc_node, kind_kw)
                self._export_decl(exported, name, i, line)
        elif t == "expression_statement":
            e = stmt.named_children[0] if stmt.named_children else None
            if e is not None and e.type == "assignment_expression":
                self.cjs_export(e, stmt)
        elif t in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            nm = stmt.child_by_field_name("name")
            if nm is not None:
                kind = {"interface_declaration": "interface", "type_alias_declaration": "type",
                        "enum_declaration": "enum"}[t]
                i = self.add(self._span_sym(stmt, name=_t(src, nm), qualname=_t(src, nm), kind=kind, parent=None,
                                            signature=_WS.sub(" ", _t(src, stmt).split("{")[0])[:240], params=[],
                                            jsdoc=_jsdoc_before(doc_node, src)))
                self._export_decl(exported, _t(src, nm), i, line)

    def _is_require_like(self, val) -> bool:
        n = val
        while n is not None and n.type in ("member_expression", "await_expression"):
            n = n.child_by_field_name("object") or (n.named_children[0] if n.named_children else None)
        if n is None or n.type != "call_expression":
            return False
        fn = n.child_by_field_name("function")
        return fn is not None and (fn.type == "import" or (fn.type == "identifier" and _t(self.src, fn) == "require"))

    def esm_export(self, stmt) -> None:
        src = self.src
        line = stmt.start_point.row + 1
        is_default = _has_kw(stmt, "default")
        source = stmt.child_by_field_name("source")
        decl = stmt.child_by_field_name("declaration")
        if source is not None:
            spec = _str_value(source, src)
            if spec is None:
                return
            clause = next((c for c in stmt.named_children if c.type == "export_clause"), None)
            ns = next((c for c in stmt.named_children if c.type == "namespace_export"), None)
            if clause is None and ns is None:
                self.exps.append(Exp("*", "reexport_all", line, spec=spec))
                self.imps.append(Imp(spec, "reexport", line, stmt.start_byte, stmt.end_byte))
            elif ns is not None:
                alias = ns.named_children[-1] if ns.named_children else None
                self.exps.append(Exp(_t(src, alias) if alias is not None else "*", "module", line, spec=spec))
                self.imps.append(Imp(spec, "reexport", line, stmt.start_byte, stmt.end_byte))
            else:
                names = []
                for sp in clause.named_children:
                    if sp.type != "export_specifier":
                        continue
                    nm = _t(src, sp.child_by_field_name("name"))
                    al = sp.child_by_field_name("alias")
                    names.append((nm, nm))
                    self.exps.append(Exp(_t(src, al) if al is not None else nm, "reexport", line, spec=spec,
                                         imported=nm))
                self.imps.append(Imp(spec, "reexport", line, stmt.start_byte, stmt.end_byte, names=names))
            return
        if decl is not None:
            self.statement(decl, stmt, exported="default" if is_default else "*")
            return
        clause = next((c for c in stmt.named_children if c.type == "export_clause"), None)
        if clause is not None:
            for sp in clause.named_children:
                if sp.type != "export_specifier":
                    continue
                nm = _t(src, sp.child_by_field_name("name"))
                al = sp.child_by_field_name("alias")
                self.exps.append(Exp(_t(src, al) if al is not None else nm, "local", line, local=nm))
            return
        if is_default:
            val = stmt.named_children[-1] if stmt.named_children else None
            self.export_value("default", val, stmt, line)

    def cjs_export(self, assign, stmt) -> None:
        src = self.src
        left = assign.child_by_field_name("left")
        right = assign.child_by_field_name("right")
        line = stmt.start_point.row + 1
        if _is_module_exports(left, src):
            if right is not None and right.type == "object":
                self.export_object(right)
            else:
                self.export_value("default", right, stmt, line)
            return
        if left is not None and left.type == "member_expression":
            obj = left.child_by_field_name("object")
            prop = left.child_by_field_name("property")
            if obj is not None and prop is not None and (
                    (obj.type == "identifier" and _t(src, obj) == "exports") or _is_module_exports(obj, src)):
                self.export_value(_t(src, prop), right, stmt, line)

    def export_object(self, obj) -> None:
        src = self.src
        for c in obj.named_children:
            line = c.start_point.row + 1
            if c.type == "shorthand_property_identifier":
                n = _t(src, c)
                self.exps.append(Exp(n, "local", line, local=n))
            elif c.type == "pair":
                key = _key_text(c.child_by_field_name("key"), src)
                if key is not None:
                    self.export_value(key, c.child_by_field_name("value"), c, line)
            elif c.type == "method_definition":
                key = _key_text(c.child_by_field_name("name"), src)
                if key is not None:
                    i = self.func_sym(key, c, c, c, inline=True)
                    self.exps.append(Exp(key, "inline", line, sym=i))
            elif c.type == "spread_element":
                inner = c.named_children[0] if c.named_children else None
                spec = self._require_spec(inner)
                if spec is not None:
                    self.exps.append(Exp("*", "reexport_all", line, spec=spec))
                elif inner is not None and inner.type == "identifier":
                    self.exps.append(Exp("*", "spread_local", line, local=_t(src, inner)))

    def _require_spec(self, node) -> str | None:
        if node is None or node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "identifier" or _t(self.src, fn) != "require":
            return None
        return _str_value(_first_arg(node), self.src)

    def export_value(self, name: str, val, doc_node, line: int) -> None:
        src = self.src
        if val is None:
            return
        if val.type == "identifier":
            self.exps.append(Exp(name, "local", line, local=_t(src, val)))
        elif val.type in _FUNC_TYPES:
            nm = val.child_by_field_name("name")
            sname = name if name != "default" or nm is None else _t(src, nm)
            i = self.func_sym(sname, val, doc_node, doc_node, inline=True)
            self.exps.append(Exp(name, "inline", line, sym=i))
        elif val.type in _CLASS_TYPES:
            nm = val.child_by_field_name("name")
            sname = name if name != "default" or nm is None else _t(src, nm)
            i = self.class_sym(sname, val, doc_node, doc_node, inline=True)
            self.exps.append(Exp(name, "inline", line, sym=i))
        elif val.type == "new_expression":
            ctor = val.child_by_field_name("constructor")
            self.exps.append(Exp(name, "instance", line, local=_t(src, ctor) if ctor is not None else None))
        elif val.type == "member_expression" and self._require_spec(val.child_by_field_name("object")):
            prop = val.child_by_field_name("property")
            self.exps.append(Exp(name, "reexport", line, spec=self._require_spec(val.child_by_field_name("object")),
                                 imported=_t(src, prop) if prop is not None else None))
        elif self._require_spec(val) is not None:
            spec = self._require_spec(val)
            self.exps.append(Exp(name, "module", line, spec=spec))
            if name == "default":
                self.exps.append(Exp("*", "reexport_all", line, spec=spec))
        else:
            self.exps.append(Exp(name, "value", line))

    # -- imports ---------------------------------------------------------------
    def requires_and_calls(self, root, fx: FileFacts) -> None:
        src = self.src
        for _, caps in ts.QueryCursor(self.lang.q_call).matches(root):
            call = caps["call"][0]
            fn = caps["fn"][0]
            if call.type != "call_expression":       # new X(...)
                if fn.type == "identifier":
                    cname = _t(src, fn)
                    args = call.child_by_field_name("arguments")
                    if cname == "Date" and (args is None or not args.named_children):
                        fx.io_uses.append((call.start_byte, "clock"))
                    elif cname == "Pool":
                        fx.io_uses.append((call.start_byte, "new Pool"))
                continue
            if fn.type == "member_expression":
                prop = fn.child_by_field_name("property")
                obj = fn.child_by_field_name("object")
                if prop is not None:
                    pname = _t(src, prop)
                    oname = _t(src, obj) if obj is not None and obj.type == "identifier" else None
                    if oname not in BUILTIN_RECEIVERS:
                        fx.member_calls[pname] += 1
                        if oname is not None:
                            fx.member_recv[(pname, oname)] += 1
                    if oname == "jest" and pname in JEST_MOCK_PROPS:
                        spec = _str_value(_first_arg(call), src)
                        if spec:
                            self.imps.append(Imp(spec, "jest_mock", call.start_point.row + 1, call.start_byte,
                                                 call.start_byte))
            elif fn.type == "import":
                spec = _str_value(_first_arg(call), src)
                if spec:
                    self.imps.append(Imp(spec, "dynamic", call.start_point.row + 1, call.start_byte,
                                         call.start_byte, lazy=True))
            elif fn.type == "identifier" and _t(src, fn) == "require":
                spec = _str_value(_first_arg(call), src)
                if spec is not None:
                    self.require_binding(call, spec)

    def require_binding(self, call, spec: str) -> None:
        src = self.src
        parent = call.parent
        member = None
        holder = parent
        if parent is not None and parent.type == "member_expression" and parent.child_by_field_name("object") == call:
            prop = parent.child_by_field_name("property")
            member = _t(src, prop) if prop is not None else None
            holder = parent.parent
        while holder is not None and holder.type in ("await_expression", "parenthesized_expression"):
            holder = holder.parent
        imp = Imp(spec, "require", call.start_point.row + 1, call.start_byte, call.end_byte, member=member)
        if holder is not None and holder.type == "variable_declarator":
            nm = holder.child_by_field_name("name")
            decl = holder.parent
            imp.start, imp.end = holder.start_byte, holder.end_byte
            imp.lazy = decl is None or decl.parent is None or decl.parent.type != "program"
            if nm is not None and nm.type == "identifier":
                imp.local = _t(src, nm)
            elif nm is not None and nm.type == "object_pattern":
                for p in nm.named_children:
                    if p.type == "shorthand_property_identifier_pattern":
                        n = _t(src, p)
                        imp.names.append((n, n))
                    elif p.type == "object_assignment_pattern":
                        left = p.child_by_field_name("left")
                        if left is not None:
                            n = _t(src, left)
                            imp.names.append((n, n))
                    elif p.type == "pair_pattern":
                        k = _key_text(p.child_by_field_name("key"), src)
                        v = p.child_by_field_name("value")
                        if v is not None and v.type == "assignment_pattern":
                            v = v.child_by_field_name("left")
                        if k and v is not None and v.type == "identifier":
                            imp.names.append((k, _t(src, v)))
        else:
            imp.lazy = not self._top_level(call)
        self.imps.append(imp)

    @staticmethod
    def _top_level(node) -> bool:
        n = node.parent
        while n is not None:
            if n.type in _FUNC_TYPES or n.type in _FUNC_DECL or n.type == "method_definition":
                return False
            n = n.parent
        return True

    def esm_imports(self, root) -> None:
        src = self.src
        for stmt in root.named_children:
            if stmt.type != "import_statement":
                continue
            spec = _str_value(stmt.child_by_field_name("source"), src)
            if spec is None:
                continue
            imp = Imp(spec, "import", stmt.start_point.row + 1, stmt.start_byte, stmt.end_byte)
            clause = next((c for c in stmt.named_children if c.type == "import_clause"), None)
            if clause is not None:
                for c in clause.named_children:
                    if c.type == "identifier":
                        imp.names.append(("default", _t(src, c)))
                    elif c.type == "namespace_import":
                        idn = next((x for x in c.named_children if x.type == "identifier"), None)
                        if idn is not None:
                            imp.local = _t(src, idn)
                    elif c.type == "named_imports":
                        for sp in c.named_children:
                            if sp.type != "import_specifier":
                                continue
                            nm = _t(src, sp.child_by_field_name("name"))
                            al = sp.child_by_field_name("alias")
                            imp.names.append((nm, _t(src, al) if al is not None else nm))
            self.imps.append(imp)


def parse_file(src: bytes, ext: str) -> FileFacts | None:
    """Facts for one file, or None when the extension has no grammar."""
    lang = lang_for(ext)
    if lang is None:
        return None
    tree = lang.parser.parse(src)
    root = tree.root_node
    errors = len(ts.QueryCursor(lang.q_error).captures(root).get("e", [])) if root.has_error else 0
    fx = FileFacts(lang=lang.name, lines=src.count(b"\n") + (0 if src.endswith(b"\n") or not src else 1),
                   errors=errors)
    ex = _Extractor(src, lang)
    ex.top(root)
    ex.esm_imports(root)
    ex.requires_and_calls(root, fx)
    fx.symbols, fx.imports, fx.exports = ex.syms, ex.imps, ex.exps

    order = sorted(range(len(fx.symbols)), key=lambda i: (fx.symbols[i].start, -fx.symbols[i].end))
    starts = [fx.symbols[i].start for i in order]

    def innermost(pos: int) -> int | None:
        j = bisect.bisect_right(starts, pos) - 1
        while j >= 0:
            s = fx.symbols[order[j]]
            if s.start <= pos < s.end:
                return order[j]
            j -= 1
        return None

    # Branchiness = decision points inside the span (bench/js/lib/symbols.js counts the same node kinds).
    dec = sorted(n.start_byte for n in ts.QueryCursor(lang.q_decision).captures(root).get("d", []))
    for s in fx.symbols:
        if s.kind not in ("value", "interface", "type", "enum"):
            s.branchiness = bisect.bisect_left(dec, s.end) - bisect.bisect_left(dec, s.start)
    for r in ts.QueryCursor(lang.q_return).captures(root).get("r", []):
        i = innermost(r.start_byte)
        if i is not None:
            fx.symbols[i].returns_value = True

    # Reference candidates: names bound by imports, top-level symbols, IO globals.
    excluded = sorted((i.start, i.end) for i in fx.imports if i.end > i.start)
    ex_starts = [a for a, _ in excluded]

    def in_import_decl(pos: int) -> bool:
        j = bisect.bisect_right(ex_starts, pos) - 1
        return j >= 0 and excluded[j][0] <= pos < excluded[j][1]

    bound: set[str] = set(ex.top_names)
    io_bind: dict[str, str] = {}
    member_objs = {n for n, i in ex.top_names.items() if fx.symbols[i].kind in ("class", "object")} | IO_GLOBAL_OBJECTS
    for imp in fx.imports:
        base = imp.spec[5:] if imp.spec.startswith("node:") else imp.spec
        locs = ([imp.local] if imp.local else []) + [loc for _, loc in imp.names]
        for loc in locs:
            bound.add(loc)
            member_objs.add(loc)
            if base in IO_MODULES:
                io_bind[loc] = f"module:{base}"
        if base in IO_MODULES and imp.lazy:
            fx.io_uses.append((imp.start, f"module:{base}"))

    shadow: dict[int, set[str]] = {}

    def shadowed(pos: int, name: str) -> bool:
        """A parameter of the enclosing symbol with the same name hides the binding (cheap scope check)."""
        i = innermost(pos)
        if i is None:
            return False
        if i not in shadow:
            shadow[i] = _param_names(fx.symbols[i].params)
        return name in shadow[i]

    for node in ts.QueryCursor(lang.q_ident).captures(root).get("id", []):
        name = src[node.start_byte:node.end_byte].decode("utf-8", "replace")
        if name not in bound and name not in IO_GLOBAL_CALLS:
            continue
        pos = node.start_byte
        if in_import_decl(pos) or shadowed(pos, name):
            continue
        par = node.parent
        pt = par.type if par is not None else ""
        if pt in _DECL_PARENTS and par.child_by_field_name("name") == node:
            continue
        if pt == "variable_declarator" and par.child_by_field_name("name") == node:
            continue
        if pt in _PATTERN_PARENTS:
            continue
        if pt == "member_expression" and par.child_by_field_name("object") == node:
            continue  # handled by the member pass
        if pt == "assignment_expression" and par.child_by_field_name("left") == node:
            continue
        kind = "ref"
        if pt == "call_expression" and par.child_by_field_name("function") == node:
            kind = "call"
        elif pt == "new_expression" and par.child_by_field_name("constructor") == node:
            kind = "new"
        if name not in bound:
            if kind == "call":
                fx.io_uses.append((pos, IO_GLOBAL_CALLS[name]))
            continue
        if name in io_bind:
            fx.io_uses.append((pos, io_bind[name]))
        fx.uses.append(Use(pos, node.start_point.row + 1, name, None, kind))

    for _, caps in ts.QueryCursor(lang.q_member).matches(root):
        obj, prop, m = caps["obj"][0], caps["prop"][0], caps["m"][0]
        oname = "this" if obj.type == "this" else src[obj.start_byte:obj.end_byte].decode("utf-8", "replace")
        if oname != "this" and oname not in member_objs:
            continue
        pos = m.start_byte
        if in_import_decl(pos):
            continue
        if oname != "this" and shadowed(pos, oname):
            continue
        pname = src[prop.start_byte:prop.end_byte].decode("utf-8", "replace")
        if oname in IO_GLOBAL_OBJECTS and oname not in bound:
            tag = IO_MEMBERS.get((oname, pname))
            if tag:
                fx.io_uses.append((pos, tag))
            continue
        par = m.parent
        pt = par.type if par is not None else ""
        if pt == "assignment_expression" and par.child_by_field_name("left") == m:
            continue  # this.x = ... (a write, not a use)
        kind = "ref"
        if pt == "call_expression" and par.child_by_field_name("function") == m:
            kind = "call"
        elif pt == "new_expression" and par.child_by_field_name("constructor") == m:
            kind = "new"
        if oname in io_bind:
            fx.io_uses.append((pos, io_bind[oname]))
        fx.uses.append(Use(pos, m.start_point.row + 1, oname, pname, kind))

    for pos, tag in fx.io_uses:
        i = innermost(pos)
        if i is not None:
            fx.symbols[i].io.add(tag)
    fx.use_owner = [innermost(u.pos) for u in fx.uses]
    return fx
