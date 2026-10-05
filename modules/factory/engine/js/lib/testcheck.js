// Vendored from bench/js/lib/testcheck.js (agent-array M1 benchmark,). The bench copy is NOT hardened:
// the 'engine hardening' block below (marked) exists only here; keep the rest in step.
'use strict';
// Static gates for a generated jest test file. Nothing here executes the candidate.
//   scope          requires the target module, references every target symbol,
//                  does not mock/stub the code under test
//   free_ident     every identifier resolves; requires resolve to real files / declared
//                  packages; destructured / member imports exist in the module's exports
//   hygiene        no .only/.skip/.todo/x*/f*, no snapshots, no placeholders,
//                  no child_process / process.exit
//                  (engine hardening) no network / shell / module-loader / vm / fs reach, no aliased require,
//                  no eval / Function, no stringifying the code under test, no @jest-environment pragma,
//                  no control or bidi characters. The Docker/tester sandbox is the real boundary; these checks
//                  keep tests that are never legitimate unit tests from passing the mutant gate by reading
//                  the source text or detecting a mutant run.
//   assert_strength every test has at least one value assertion (not just
//                  not.toThrow / truthiness / typeof / instanceOf)
const fs = require('fs');
const path = require('path');
const walk = require('acorn-walk');
const { parse } = require('./parse');
const { analyze } = require('./scope');
const { TEST_GLOBALS, NODE_BUILTINS } = require('./globals');
const { collectRequires, exportsOf, resolveRelative } = require('./modinfo');
const { badChar } = require('./docsplice');

const TEST_FNS = new Set(['it', 'test']);
const SUITE_FNS = new Set(['describe']);
const FOCUS_SKIP_IDS = new Set(['fit', 'fdescribe', 'xit', 'xdescribe', 'xtest', 'pending']);
const FOCUS_SKIP_PROPS = new Set(['only', 'skip', 'todo', 'failing']);
const SNAPSHOT = new Set(['toMatchSnapshot', 'toMatchInlineSnapshot', 'toThrowErrorMatchingSnapshot', 'toThrowErrorMatchingInlineSnapshot']);
const WEAK_MATCHERS = new Set(['toBeDefined', 'toBeTruthy', 'toBeFalsy', 'toBeInstanceOf']);
const WEAK_WHEN_NOT = new Set(['toThrow', 'toThrowError', 'toBeNull', 'toBeUndefined', 'toBeDefined', 'toBeTruthy', 'toBeFalsy', 'toBeNaN', 'toHaveBeenCalled']);
const PLACEHOLDER_RE = /\b(TODO|FIXME|XXX|TBD)\b|implement (this|me)|your code here|add (more )?(tests?|cases?) here|rest of (the )?(tests?|cases?)|similar(ly)? for (the )?(other|remaining)|more tests (can|could|would) be/i;
const FORBIDDEN_MODULES = new Set(['child_process', 'node:child_process', 'cluster', 'node:cluster']);
// ---- engine hardening ----
const BANNED_MODULES = new Set(['child_process', 'cluster', 'vm', 'worker_threads', 'module', 'http', 'https', 'http2', 'net',
  'dgram', 'dns', 'tls', 'inspector', 'repl', 'v8', 'wasi', 'trace_events', 'async_hooks', 'diagnostics_channel']);
const FS_MODULES = new Set(['fs', 'fs/promises']);
const NETWORK_GLOBALS = new Set(['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'navigator']);
const BANNED_PROCESS_PROPS = new Set(['mainModule', 'binding', '_linkedBinding', 'dlopen', 'cwd', 'chdir', 'kill', 'abort', 'reallyExit', 'umask']);
const JEST_REQUIRE_FNS = new Set(['requireActual', 'requireMock', 'createMockFromModule', 'genMockFromModule']);
const JEST_MOCK_FNS = new Set(['mock', 'doMock']);
const modBase = (s) => String(s).replace(/^node:/, '');

function fail(stage, message, detail) { return { ok: false, stage, message, detail }; }

function pkgName(spec) {
  const parts = spec.split('/');
  return spec.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}

/** Walk expect(...).a.b.matcher(...) chains. Returns {matcher, mods, args, subject} or null. */
function expectChain(call) {
  if (call.type !== 'CallExpression' || call.callee.type !== 'MemberExpression' || call.callee.computed) return null;
  const matcher = call.callee.property.name;
  const mods = [];
  let o = call.callee.object;
  while (o && o.type === 'MemberExpression' && !o.computed) { mods.unshift(o.property.name); o = o.object; }
  if (!o || o.type !== 'CallExpression' || o.callee.type !== 'Identifier' || o.callee.name !== 'expect') return null;
  return { matcher, mods, args: call.arguments, subject: o.arguments[0] || null };
}

function isAsymmetricAny(n) {
  return n && n.type === 'CallExpression' && n.callee.type === 'MemberExpression' &&
    n.callee.object.type === 'Identifier' && n.callee.object.name === 'expect' &&
    ['anything', 'any'].includes(n.callee.property.name) &&
    (n.callee.property.name === 'anything' || (n.arguments[0] && n.arguments[0].type === 'Identifier' && ['Object', 'Function'].includes(n.arguments[0].name)));
}

/** Classify one assertion as 'strong' or 'weak:<reason>'. */
function classify(ch, src) {
  const { matcher, mods, args, subject } = ch;
  const not = mods.includes('not');
  if (SNAPSHOT.has(matcher)) return 'weak:snapshot';
  if (subject && subject.type === 'Literal') return 'weak:literal-subject';
  if (subject && subject.type === 'UnaryExpression' && subject.operator === 'typeof') return 'weak:typeof';
  if (WEAK_MATCHERS.has(matcher) && !not) return `weak:${matcher}`;
  if (not && WEAK_WHEN_NOT.has(matcher) && args.length === 0) return `weak:not.${matcher}`;
  if (not && (matcher === 'toBe' || matcher === 'toEqual') && args[0] &&
    ((args[0].type === 'Identifier' && args[0].name === 'undefined') || (args[0].type === 'Literal' && args[0].value === null))) return 'weak:not-null-check';
  if ((matcher === 'toThrow' || matcher === 'toThrowError') && args.length === 0) return 'weak:bare-toThrow';
  if (matcher === 'toHaveProperty' && args.length < 2) return 'weak:toHaveProperty-no-value';
  if (args.some(isAsymmetricAny)) return 'weak:expect.anything';
  if (subject && args[0] && !not && (matcher === 'toBe' || matcher === 'toEqual' || matcher === 'toStrictEqual') &&
    src.slice(subject.start, subject.end) === src.slice(args[0].start, args[0].end)) return 'weak:tautology';
  return 'strong';
}

/** Find test cases: it/test(name, fn), it.each(...)(name, fn), test.concurrent(name, fn). */
function findTests(ast, src) {
  const tests = [];
  walk.simple(ast, {
    CallExpression(n) {
      const c = n.callee;
      let base = null;
      if (c.type === 'Identifier' && TEST_FNS.has(c.name)) base = c.name;
      else if (c.type === 'MemberExpression' && !c.computed && c.object.type === 'Identifier' && TEST_FNS.has(c.object.name) &&
        ['concurrent', 'only', 'skip', 'failing'].includes(c.property.name)) base = c.object.name;
      else if ((c.type === 'CallExpression' || c.type === 'TaggedTemplateExpression')) {
        const inner = c.type === 'CallExpression' ? c.callee : c.tag;
        if (inner.type === 'MemberExpression' && !inner.computed && inner.property.name === 'each') {
          const ob = inner.object;
          if (ob.type === 'Identifier' && TEST_FNS.has(ob.name)) base = ob.name;
          if (ob.type === 'MemberExpression' && ob.object.type === 'Identifier' && TEST_FNS.has(ob.object.name)) base = ob.object.name;
        }
      }
      if (!base) return;
      const fn = n.arguments.find((a) => a.type === 'ArrowFunctionExpression' || a.type === 'FunctionExpression');
      const title = n.arguments[0] && n.arguments[0].type === 'Literal' ? String(n.arguments[0].value)
        : (n.arguments[0] && n.arguments[0].type === 'TemplateLiteral' ? src.slice(n.arguments[0].start + 1, n.arguments[0].end - 1) : '(dynamic title)');
      tests.push({ title, fn, node: n, line: n.loc.start.line });
    },
  });
  return tests;
}

/** Assertions inside a node, classified. */
function assertionsIn(node, src) {
  const out = [];
  walk.simple(node, {
    CallExpression(n) {
      const ch = expectChain(n);
      if (ch) out.push({ line: n.loc.start.line, matcher: ch.matcher, mods: ch.mods, cls: classify(ch, src) });
    },
  });
  return out;
}

/** Helper functions declared in the file (top level or inside describe) that contain strong assertions. */
function assertHelpers(ast, src) {
  const helpers = new Set();
  walk.simple(ast, {
    FunctionDeclaration(n) { if (n.id && assertionsIn(n.body, src).some((a) => a.cls === 'strong')) helpers.add(n.id.name); },
    VariableDeclarator(n) {
      if (n.id.type === 'Identifier' && n.init && (n.init.type === 'ArrowFunctionExpression' || n.init.type === 'FunctionExpression') &&
        assertionsIn(n.init.body, src).some((a) => a.cls === 'strong')) helpers.add(n.id.name);
    },
  });
  return helpers;
}

function callsHelper(node, helpers) {
  let hit = false;
  walk.simple(node, {
    CallExpression(n) { if (n.callee.type === 'Identifier' && helpers.has(n.callee.name)) hit = true; },
  });
  return hit;
}

/**
 * Engine hardening (not in the bench copy): constructs that are never part of a legitimate unit test of a
 * pure function, and that would let a test reach outside the sandbox or pass the mutant gate without
 * exercising behaviour. Best-effort static checks: the isolated runner (offline container, read-only
 * rootfs, no capabilities) is the actual boundary, and the mutant gate catches the rest.
 * @returns {string[]} hygiene messages
 */
function hardening(a, ast, comments, src, requires) {
  const out = [];
  const ln = (n) => (n && n.loc ? n.loc.start.line : '?');
  const names = new Set();
  for (const s of a.symbols) for (const part of s.split('.')) names.add(part.replace(/^(get|set):/, ''));
  names.delete('constructor');
  const isTargetRef = (n) => !!n && ((n.type === 'Identifier' && names.has(n.name)) ||
    (n.type === 'MemberExpression' && ((!n.computed && names.has(n.property.name)) ||
      (n.computed && n.property.type === 'Literal' && names.has(String(n.property.value))))));

  // modules: banned outright; fs only when it is mocked (a real fs read can inspect the code under test)
  let mockedFs = false;
  const fsUses = [];
  const checkSpec = (spec, line, via) => {
    const b = modBase(spec);
    if (BANNED_MODULES.has(b) || BANNED_MODULES.has(b.split('/')[0])) out.push(`line ${line}: ${via}('${spec}') is not allowed in tests (shell, network, module loader or vm access)`);
    else if (FS_MODULES.has(b)) fsUses.push({ line, via, spec });
  };
  for (const r of requires) if (r.spec) checkSpec(r.spec, r.line, 'require');
  const free = analyze(ast, new Set()).free;       // every undeclared reference, including real globals
  const freeNodes = new Set();
  for (const nodes of free.values()) for (const n of nodes) freeNodes.add(n);
  const isFree = (n) => !!n && n.type === 'Identifier' && freeNodes.has(n);
  const requireCallees = new Set();

  walk.simple(ast, {
    CallExpression(n) {
      const c = n.callee;
      if (c.type === 'Identifier' && c.name === 'require') requireCallees.add(c);
      if (isFree(c) && c.name === 'eval') out.push(`line ${ln(n)}: eval() is not allowed in tests`);
      if (isFree(c) && c.name === 'Function') out.push(`line ${ln(n)}: Function() is not allowed in tests`);
      if (c.type === 'MemberExpression' && !c.computed && isFree(c.object) && c.object.name === 'jest') {
        const f = c.property.name;
        const arg = n.arguments[0];
        if (JEST_REQUIRE_FNS.has(f)) {
          if (!arg || arg.type !== 'Literal') out.push(`line ${ln(n)}: jest.${f}() with a non-literal module name`);
          else checkSpec(arg.value, ln(n), `jest.${f}`);
        } else if (JEST_MOCK_FNS.has(f) && arg && arg.type === 'Literal' && FS_MODULES.has(modBase(arg.value))) mockedFs = true;
      }
      // x.toString() / x.toString.call(x) / String(x) on a function under test: compares the source text
      if (c.type === 'MemberExpression' && !c.computed && c.property.name === 'toString' && isTargetRef(c.object)) out.push(`line ${ln(n)}: stringifying the code under test (toString) is not allowed`);
      if (c.type === 'MemberExpression' && !c.computed && ['call', 'apply'].includes(c.property.name) && c.object.type === 'MemberExpression' &&
        !c.object.computed && c.object.property.name === 'toString' && isTargetRef(n.arguments[0])) out.push(`line ${ln(n)}: stringifying the code under test (toString.${c.property.name}) is not allowed`);
      if (isFree(c) && c.name === 'String' && isTargetRef(n.arguments[0])) out.push(`line ${ln(n)}: stringifying the code under test (String()) is not allowed`);
    },
    NewExpression(n) {
      if (isFree(n.callee) && n.callee.name === 'Function') out.push(`line ${ln(n)}: new Function() is not allowed in tests`);
    },
    ImportExpression(n) {
      if (n.source.type === 'Literal') checkSpec(n.source.value, ln(n), 'import');
      else out.push(`line ${ln(n)}: import() with a non-literal module name`);
    },
    ImportDeclaration(n) { checkSpec(n.source.value, ln(n), 'import'); },
    ExportAllDeclaration(n) { if (n.source) checkSpec(n.source.value, ln(n), 'export from'); },
    ExportNamedDeclaration(n) { if (n.source) checkSpec(n.source.value, ln(n), 'export from'); },
    TemplateLiteral(n) {
      if (n.expressions.some(isTargetRef)) out.push(`line ${ln(n)}: stringifying the code under test (template literal) is not allowed`);
    },
    MemberExpression(n) {
      // process.mainModule / binding / cwd ... and computed process[...]; x.constructor.constructor (the Function constructor)
      if (isFree(n.object) && n.object.name === 'process') {
        const pn = !n.computed ? n.property.name : (n.property.type === 'Literal' ? String(n.property.value) : '(computed)');
        if (pn === '(computed)' || BANNED_PROCESS_PROPS.has(pn)) out.push(`line ${ln(n)}: process.${pn} is not allowed in tests (escape hatch / environment sniffing)`);
      }
      if (!n.computed && n.property.name === 'constructor' && n.object.type === 'MemberExpression' && !n.object.computed && n.object.property.name === 'constructor') {
        out.push(`line ${ln(n)}: .constructor.constructor (the Function constructor) is not allowed`);
      }
      if (isFree(n.object) && n.object.name === 'jest' && n.computed) out.push(`line ${ln(n)}: computed access on jest is not allowed`);
    },
  });
  // require used as a value (aliased, passed around, require.resolve / require.main / require.cache)
  for (const n of free.get('require') || []) if (!requireCallees.has(n)) out.push(`line ${ln(n)}: require used other than as require('...') (aliasing, require.resolve, require.main and require.cache are not allowed)`);
  for (const n of free.get('module') || []) out.push(`line ${ln(n)}: the CommonJS \`module\` object is not allowed in tests (module.require / module.constructor reach the loader)`);
  for (const g of NETWORK_GLOBALS) for (const n of free.get(g) || []) out.push(`line ${ln(n)}: ${g} is not allowed in tests (no network)`);
  if (fsUses.length && !mockedFs) {
    out.push(`line ${fsUses[0].line}: ${fsUses[0].via}('${fsUses[0].spec}') with the real file system: a read can inspect the code under test; mock it (jest.mock('fs')) or pass data in`);
  }
  for (const cm of comments) if (/@jest-environment/.test(cm.value)) out.push(`line ${ln(cm)}: a @jest-environment pragma can load code from the repo as the test environment`);
  const bc = badChar(src);
  if (bc !== -1) out.push(`control, bidi or line-separator character U+${bc.toString(16).toUpperCase().padStart(4, '0')} in the test file`);
  return [...new Set(out)];
}

/**
 * Run all static gates.
 * @param {object} a
 * @param {string} a.code candidate test file
 * @param {string} a.repoBase absolute repo dir (read-only base copy)
 * @param {string} a.testPath repo-relative path the test will be written to
 * @param {string} a.targetFile repo-relative target module
 * @param {string[]} a.symbols target symbols (fn | Class.method)
 * @param {string[]} [a.allowedPackages] extra bare specifiers allowed
 */
function staticGates(a) {
  const checks = {};
  const t0 = Date.now();
  let parsed;
  try { parsed = parse(a.code, { sourceType: 'auto' }); } catch (e) {
    return { result: fail('parse', `SyntaxError: ${e.message}`), checks };
  }
  const { ast, comments } = parsed;
  const src = a.code;
  checks.parse = { ok: true, ms: Date.now() - t0, sourceType: parsed.sourceType };

  const testAbs = path.join(a.repoBase, a.testPath);
  const targetAbs = path.join(a.repoBase, a.targetFile);
  const pkg = JSON.parse(fs.readFileSync(path.join(a.repoBase, 'package.json'), 'utf8'));
  const declared = new Set([...Object.keys(pkg.dependencies || {}), ...Object.keys(pkg.devDependencies || {}), ...(a.allowedPackages || [])]);
  const requires = collectRequires(ast);

  // ---- hygiene (cheap, and its messages are the most actionable) ----
  const hyg = [];
  walk.simple(ast, {
    CallExpression(n) {
      const c = n.callee;
      if (c.type === 'Identifier' && FOCUS_SKIP_IDS.has(c.name)) hyg.push(`line ${n.loc.start.line}: ${c.name}() is not allowed`);
      let m = c;
      while (m && (m.type === 'MemberExpression' || m.type === 'CallExpression' || m.type === 'TaggedTemplateExpression')) {
        // it['skip'] / it['on' + 'ly'] are the same thing as it.skip: a computed property on a test function
        // is allowed only when it is a literal that is not skip/only/todo/failing
        const pn = m.type !== 'MemberExpression' ? null
          : (!m.computed ? m.property.name : (m.property.type === 'Literal' ? String(m.property.value) : '(computed)'));
        if (m.type === 'MemberExpression' && pn !== null && (pn === '(computed)' || FOCUS_SKIP_PROPS.has(pn))) {
          let root = m.object;
          while (root && root.type === 'MemberExpression') root = root.object;
          if (root && root.type === 'Identifier' && (TEST_FNS.has(root.name) || SUITE_FNS.has(root.name))) {
            hyg.push(`line ${n.loc.start.line}: ${root.name}.${pn} is not allowed`);
            break;
          }
        }
        m = m.type === 'MemberExpression' ? m.object : (m.type === 'CallExpression' ? m.callee : m.tag);
      }
      const ch = expectChain(n);
      if (ch && SNAPSHOT.has(ch.matcher)) hyg.push(`line ${n.loc.start.line}: snapshot assertion ${ch.matcher} (snapshot-only tests are not accepted)`);
      if (c.type === 'MemberExpression' && c.object.type === 'Identifier' && c.object.name === 'process' && !c.computed && c.property.name === 'exit') hyg.push(`line ${n.loc.start.line}: process.exit()`);
    },
  });
  for (const cm of comments) if (PLACEHOLDER_RE.test(cm.value)) hyg.push(`line ${cm.loc.start.line}: placeholder comment "${cm.value.trim().slice(0, 60)}"`);
  if (/^\s*\.\.\.\s*$/m.test(src)) hyg.push('a line containing only "..." (elided code)');
  for (const r of requires) if (r.spec && FORBIDDEN_MODULES.has(r.spec)) hyg.push(`line ${r.line}: require('${r.spec}') is not allowed in tests`);
  hyg.push(...hardening(a, ast, comments, src, requires));
  if (/JEST_WORKER_ID/.test(src)) hyg.push('test-environment sniffing (JEST_WORKER_ID)');
  if (hyg.length) return { result: fail('hygiene', hyg.slice(0, 8).join('; '), hyg), checks };
  checks.hygiene = { ok: true };

  // ---- free identifiers + import resolution ----
  const { free } = analyze(ast, TEST_GLOBALS);
  const fi = [];
  for (const [name, nodes] of free) fi.push(`\`${name}\` is not defined (line ${nodes[0].loc.start.line})`);
  let targetLocal = null;
  const targetNames = new Set();
  const moduleLocals = new Map(); // local -> resolved file
  for (const r of requires) {
    if (r.dynamic) { fi.push(`line ${r.node.loc.start.line}: require() with a non-literal argument`); continue; }
    if (r.spec.startsWith('.') || r.spec.startsWith('/')) {
      const res = r.spec.startsWith('/') ? null : resolveRelative(testAbs, r.spec);
      if (!res) { fi.push(`line ${r.line}: require('${r.spec}') does not resolve to a file in the repo`); continue; }
      if (/\.test\.js$/.test(res) || res.includes(`${path.sep}node_modules${path.sep}`)) { fi.push(`line ${r.line}: require('${r.spec}') must not import a test file or node_modules by path`); continue; }
      const ex = exportsOf(res);
      const checkName = (n, how) => {
        if (!n) return;
        if (!ex.opaque && !ex.defaultIdent && !ex.names.includes(n)) fi.push(`line ${r.line}: \`${n}\` is not exported by ${r.spec} (${how}); exports: ${ex.names.slice(0, 12).join(', ')}`);
      };
      for (const nm of r.names) checkName(nm.imported, 'destructured');
      if (r.member) checkName(r.member, 'member');
      if (r.local) moduleLocals.set(r.local, { file: res, ex, spec: r.spec, line: r.line });
      if (res === targetAbs) {
        if (r.local) targetLocal = r.local;
        for (const nm of r.names) if (nm.imported) targetNames.add(nm.imported);
        if (r.member) targetNames.add(r.member);
      }
    } else {
      const name = pkgName(r.spec);
      if (!NODE_BUILTINS.has(r.spec) && !NODE_BUILTINS.has(name) && !declared.has(name)) fi.push(`line ${r.line}: package '${name}' is not a declared dependency of this repo`);
    }
  }
  // member access on a module binding: mod.X must be exported
  walk.simple(ast, {
    MemberExpression(n) {
      if (n.computed || n.object.type !== 'Identifier' || !moduleLocals.has(n.object.name)) return;
      const m = moduleLocals.get(n.object.name);
      if (!m.ex.opaque && !m.ex.defaultIdent && !m.ex.names.includes(n.property.name)) fi.push(`line ${n.loc.start.line}: \`${n.object.name}.${n.property.name}\` is not exported by ${m.spec}`);
      if (m.file === targetAbs) targetNames.add(n.property.name);
    },
  });
  if (fi.length) return { result: fail('free_ident', fi.slice(0, 8).join('; '), fi), checks };
  checks.free_ident = { ok: true };

  // ---- scope: the test must exercise the target, and must not stub it ----
  const sc = [];
  const importsTarget = requires.some((r) => r.spec && r.spec.startsWith('.') && resolveRelative(testAbs, r.spec) === targetAbs);
  if (!importsTarget) sc.push(`the test never requires the target module (${a.targetFile}); require it relative to ${a.testPath}`);
  const memberNames = new Set();
  const idNames = new Set();
  const stubbed = new Set();
  walk.simple(ast, {
    MemberExpression(n) { if (!n.computed) memberNames.add(n.property.name); else if (n.property.type === 'Literal') memberNames.add(String(n.property.value)); },
    Identifier(n) { idNames.add(n.name); },
    CallExpression(n) {
      const c = n.callee;
      if (c.type === 'MemberExpression' && c.object.type === 'Identifier' && c.object.name === 'jest' &&
        ['mock', 'doMock', 'setMock'].includes(c.property.name) && n.arguments[0] && n.arguments[0].type === 'Literal') {
        const spec = String(n.arguments[0].value);
        if (spec.startsWith('.') && resolveRelative(testAbs, spec) === targetAbs) sc.push(`line ${n.loc.start.line}: jest.${c.property.name}() of the module under test`);
      }
      if (c.type === 'MemberExpression' && c.object.type === 'Identifier' && c.object.name === 'jest' && c.property.name === 'spyOn' && n.arguments[1] && n.arguments[1].type === 'Literal') {
        const meth = String(n.arguments[1].value);
        const after = src.slice(n.end, n.end + 60);
        if (a.symbols.some((s) => s.split('.').pop() === meth) &&
          /^\s*\.(mockImplementation|mockReturnValue|mockResolvedValue|mockRejectedValue)/.test(after)) {
          stubbed.add(meth); // stubbing one target while testing another is fine; stubbing all of them is not
        }
      }
    },
  });
  const targetMethods = a.symbols.map((s) => s.split('.').pop());
  if (targetMethods.length && targetMethods.every((m) => stubbed.has(m))) {
    sc.push(`every function under test (${targetMethods.join(', ')}) is replaced by a mock implementation`);
  }
  for (const sym of a.symbols) {
    const parts = sym.split('.');
    const meth = parts.length > 1 ? parts[parts.length - 1].replace(/^(get|set):/, '') : null;
    if (meth === 'constructor') {
      if (!idNames.has(parts[0]) && !memberNames.has(parts[0])) sc.push(`target ${sym} is never exercised (no reference to ${parts[0]})`);
    } else if (meth) {
      if (!memberNames.has(meth)) sc.push(`target ${sym} is never called (no \`.${meth}\` in the test)`);
      if (!idNames.has(parts[0]) && !memberNames.has(parts[0]) && !targetNames.has(parts[0])) sc.push(`class ${parts[0]} (for ${sym}) is never referenced`);
    } else if (!idNames.has(sym) && !memberNames.has(sym)) sc.push(`target ${sym} is never called`);
  }
  if (sc.length) return { result: fail('scope', sc.join('; '), sc), checks };
  checks.scope = { ok: true, targetLocal };

  // ---- assertion strength floor ----
  const tests = findTests(ast, src);
  if (!tests.length) return { result: fail('assert_strength', 'no it()/test() cases found'), checks };
  const helpers = assertHelpers(ast, src);
  const weak = [];
  const perTest = [];
  for (const t of tests) {
    if (!t.fn) { weak.push(`"${t.title}" has no test function`); continue; }
    const as = assertionsIn(t.fn.body, src);
    const strong = as.filter((x) => x.cls === 'strong').length;
    const viaHelper = !strong && callsHelper(t.fn.body, helpers);
    perTest.push({ title: t.title, line: t.line, assertions: as.length, strong, viaHelper });
    if (!strong && !viaHelper) {
      const why = as.length ? [...new Set(as.map((x) => x.cls.replace('weak:', '')))].join(', ') : 'no assertions';
      weak.push(`"${t.title}" (line ${t.line}) has no value assertion (${why})`);
    }
  }
  if (weak.length) return { result: fail('assert_strength', weak.slice(0, 6).join('; '), weak), checks };
  checks.assert_strength = { ok: true, tests: perTest.length, perTest };
  return { result: { ok: true, stage: 'static', testsFound: tests.length }, checks };
}

module.exports = { staticGates, findTests, assertionsIn, expectChain, classify };
