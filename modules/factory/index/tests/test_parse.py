"""Parser (tree-sitter extraction) on small, targeted snippets."""
from estate_index.jsparse import parse_file


def facts(src: str, ext: str = ".js"):
    return parse_file(src.encode(), ext)


def by_qual(fx):
    return {s.qualname: s for s in fx.symbols}


def test_module_exports_object_forms():
    fx = facts("""
const { a, b: bb } = require('./a');
function local() {}
module.exports = {
  local, renamed: local, a,
  inline() { return 1; },
  arrow: (x) => x,
  fn: function () {},
  ...require('./more'),
  sub: require('./sub'),
  picked: require('./c').picked,
  value: 42,
};
""")
    kinds = {e.name: e.kind for e in fx.exports if e.name != "*"}
    assert kinds == {"local": "local", "renamed": "local", "a": "local", "inline": "inline", "arrow": "inline",
                     "fn": "inline", "sub": "module", "picked": "reexport", "value": "value"}
    assert [e.spec for e in fx.exports if e.kind == "reexport_all"] == ["./more"]
    q = by_qual(fx)
    assert q["inline"].inline_export and q["arrow"].inline_export and not q["local"].inline_export


def test_other_cjs_and_esm_exports():
    fx = facts("exports.x = () => 1;\nmodule.exports.y = y;\nfunction y() {}\n")
    assert {(e.name, e.kind) for e in fx.exports} == {("x", "inline"), ("y", "local")}
    fx = facts("module.exports = new Store();\nclass Store { get() { return 1; } }\n")
    assert [(e.name, e.kind, e.local) for e in fx.exports] == [("default", "instance", "Store")]
    fx = facts("export function f() {}\nexport const g = () => 1;\nexport { h as k };\n"
               "export * from './all';\nexport default class C {}\nfunction h() {}\n", ".mjs")
    got = {(e.name, e.kind) for e in fx.exports}
    assert {("f", "inline"), ("g", "inline"), ("k", "local"), ("*", "reexport_all"), ("default", "inline")} <= got


def test_require_bindings():
    fx = facts("""
const fs = require('fs');
const { a, b: renamed, c = 1 } = require('./x');
const one = require('./y').one;
function lazy() { const p = require('path'); return p; }
require('./side-effect');
jest.mock('./mocked');
""")
    imps = {i.spec: i for i in fx.imports}
    assert imps["fs"].local == "fs" and not imps["fs"].lazy
    assert imps["./x"].names == [("a", "a"), ("b", "renamed"), ("c", "c")]
    assert imps["./y"].local == "one" and imps["./y"].member == "one"
    assert imps["path"].lazy
    assert imps["./side-effect"].local is None and imps["./side-effect"].names == []
    assert imps["./mocked"].kind == "jest_mock"


def test_symbols_signatures_jsdoc_and_branchiness():
    fx = facts("""
/**
 * Adds.
 * @param {number} a first
 * @param {{b?: number}} [opts]
 * @returns {number}
 */
async function add(a, { b } = {}) {
  if (a && b) return a ?? b;
  for (const x of [1]) { try { x(); } catch (e) { return 0; } }
  return a > 1 ? 1 : 2;
}
class K extends Base {
  constructor(db) { super(); this.db = db; }
  static make() { return new K(); }
  get size() { return this.items?.length; }
  async run(z) { switch (z) { case 1: return this.helper(); default: return null; } }
  helper() { return 1; }
  handler = () => this.run(1);
}
""")
    q = by_qual(fx)
    add = q["add"]
    assert add.is_async and add.signature == "async add(a, { b } = {})"
    assert add.jsdoc and [p["type"] for p in add.jsdoc["params"]] == ["number", "{b?: number}"]
    assert add.jsdoc["returns"] == "number"
    # if, &&, ??, for-of, catch, ternary
    assert add.branchiness == 6 and add.returns_value
    assert q["K"].signature == "class K extends Base"
    assert q["K.make"].is_static and q["K.size"].kind == "getter" and q["K.constructor"].kind == "constructor"
    assert q["K.run"].branchiness == 1        # one `case` with a value; default does not count
    assert q["K.handler"].kind == "method"   # function-valued class field


def test_uses_this_member_and_shadowing():
    fx = facts("""
const { helper } = require('./h');
const ns = require('./ns');
function top() { helper(); ns.go(); return new Thing(); }
function shadow(helper) { return helper(); }
class Thing { a() { return this.b(); } b() { return 1; } }
""")
    uses = {(u.name, u.member, u.kind, fx.symbols[o].qualname if o is not None else None)
            for u, o in zip(fx.uses, fx.use_owner)}
    assert ("helper", None, "call", "top") in uses
    assert ("ns", "go", "call", "top") in uses
    assert ("Thing", None, "new", "top") in uses
    assert ("this", "b", "call", "Thing.a") in uses
    assert not any(u[0] == "helper" and u[3] == "shadow" for u in uses)   # parameter shadows the import


def test_io_tags_match_engine_inventory():
    fx = facts("""
const fs = require('fs');
const { Pool } = require('pg');
function a() { return fs.readFileSync('x'); }
function b() { return Date.now() + Math.random(); }
function c() { setTimeout(() => {}, 1); return process.env.X; }
function d() { return new Pool(); }
function e() { return new Date(); }
function f() { return new Date(5); }
""")
    q = by_qual(fx)
    assert q["a"].io == {"module:fs"}
    assert q["b"].io == {"clock", "Math.random"}
    assert q["c"].io == {"setTimeout", "process.env"}
    assert "new Pool" in q["d"].io and "module:pg" in q["d"].io
    assert q["e"].io == {"clock"} and q["f"].io == set()


def test_typescript_and_parse_errors():
    fx = facts("export interface P { a: number }\nexport function f(x: number): string { return `${x}`; }\n", ".ts")
    q = by_qual(fx)
    assert q["P"].kind == "interface" and q["f"].signature == "f(x: number): string"
    bad = facts("function ok() {}\nfunction broken( {\n")
    assert bad.errors > 0 and "ok" in by_qual(bad)
