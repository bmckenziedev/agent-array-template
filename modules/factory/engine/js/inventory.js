'use strict';
// Repo inventory for the factory packer and the generator (template expansion).
// Usage: node inventory.js --base <repoDir> [--roots src,lib] [--files a.js,b.js]  -> JSON on stdout
// PARSE ONLY: never requires, evaluates or runs anything from the repo, so it is safe on the host.
// Adapted from bench/js/inventory.js ().
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const walk = require('acorn-walk');
const { parse } = require('./lib/parse');
const { analyze } = require('./lib/scope');
const { MODULE_GLOBALS } = require('./lib/globals');
const { collectRequires, collectExports } = require('./lib/modinfo');
const { listSymbols, branchiness, ownReturns } = require('./lib/symbols');
const { collectSites, pickMutants } = require('./lib/mutate');

const IO_MODULES = new Set(['fs', 'fs/promises', 'child_process', 'net', 'http', 'https', 'tls', 'dgram', 'pg', 'ws',
  'node:fs', 'node:fs/promises', 'node:child_process', 'node:net', 'node:http', 'node:https']);
const SKIP_DIRS = new Set(['node_modules', '__tests__', '__mocks__', 'coverage', 'dist', 'build', 'vendor']);

function args(argv) {
  const out = { roots: null, files: null, base: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--base') out.base = argv[++i];
    else if (a === '--roots') out.roots = argv[++i].split(',').filter(Boolean);
    else if (a === '--files') out.files = argv[++i].split(',').filter(Boolean);
  }
  if (!out.base) throw new Error('--base is required');
  return out;
}

function isSourceFile(name) {
  return /\.(c?js)$/.test(name) && !/\.(test|spec)\.c?js$/.test(name) && !name.endsWith('.min.js');
}

function listJs(dir, roots) {
  const out = [];
  const visit = (d) => {
    let entries;
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { return; }
    for (const e of entries) {
      if (e.name.startsWith('.') || SKIP_DIRS.has(e.name)) continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) visit(p);
      else if (e.isFile() && isSourceFile(e.name)) out.push(p);
    }
  };
  for (const r of roots) {
    const p = path.join(dir, r);
    if (fs.existsSync(p) && fs.statSync(p).isDirectory()) visit(p);
    else if (fs.existsSync(p) && isSourceFile(path.basename(p))) out.push(p);
  }
  return [...new Set(out)].sort();
}

function sha256(s) { return crypto.createHash('sha256').update(s, 'utf8').digest('hex'); }

function signatureText(src, sym) {
  const fn = sym.fn;
  const head = src.slice(sym.decl.start, fn.body.start).replace(/\s+$/, '');
  if (fn.expression) return src.slice(sym.decl.start, fn.body.end);
  return `${head} { … }`;
}

function eolOf(src) {
  const crlf = (src.match(/\r\n/g) || []).length;
  const lf = (src.match(/\n/g) || []).length - crlf;
  return crlf > lf ? 'crlf' : 'lf';
}

function fileInfo(repoDir, file) {
  const raw = fs.readFileSync(file, 'utf8');
  const src = raw;
  const rel = path.relative(repoDir, file).split(path.sep).join('/');
  let parsed;
  try { parsed = parse(src, { sourceType: 'auto' }); } catch (e) { return { file: rel, error: String(e.message) }; }
  const { ast, comments } = parsed;
  const scope = analyze(ast, MODULE_GLOBALS);
  const requires = collectRequires(ast);
  const exp = collectExports(ast);
  const symbols = listSymbols(src, ast, comments);

  const bindings = [];
  const requireStmts = [];
  for (const st of ast.body) {
    if (st.type === 'VariableDeclaration') {
      const isReq = st.declarations.some((d) => d.init && (
        (d.init.type === 'CallExpression' && d.init.callee.name === 'require') ||
        (d.init.type === 'MemberExpression' && d.init.object.type === 'CallExpression' && d.init.object.callee.name === 'require')));
      if (isReq) { requireStmts.push({ start: st.start, end: st.end, line: st.loc.start.line }); continue; }
      for (const d of st.declarations) {
        const names = [];
        const collect = (p) => {
          if (!p) return;
          if (p.type === 'Identifier') names.push(p.name);
          else if (p.type === 'ObjectPattern') p.properties.forEach((q) => collect(q.type === 'RestElement' ? q.argument : q.value));
          else if (p.type === 'ArrayPattern') p.elements.forEach(collect);
          else if (p.type === 'AssignmentPattern') collect(p.left);
        };
        collect(d.id);
        const isFnInit = d.init && (d.init.type === 'ArrowFunctionExpression' || d.init.type === 'FunctionExpression');
        for (const n of names) bindings.push({ name: n, kind: isFnInit ? 'function' : st.kind, start: st.start, end: st.end, line: st.loc.start.line, endLine: st.loc.end.line });
      }
    } else if (st.type === 'FunctionDeclaration' && st.id) {
      bindings.push({ name: st.id.name, kind: 'function', start: st.start, end: st.end, line: st.loc.start.line, endLine: st.loc.end.line });
    } else if (st.type === 'ClassDeclaration' && st.id) {
      bindings.push({ name: st.id.name, kind: 'class', start: st.start, end: st.end, line: st.loc.start.line, endLine: st.loc.end.line });
    }
  }
  const requireLocals = new Map();
  for (const r of requires) {
    if (!r.spec) continue;
    if (r.local) requireLocals.set(r.local, r.spec);
    for (const n of r.names) if (n.local) requireLocals.set(n.local, r.spec);
  }
  const exportedNames = new Set(exp.names);
  if (exp.defaultIdent) exportedNames.add(exp.defaultIdent);

  const seen = new Map();
  const syms = symbols.map((s) => {
    const fn = s.fn;
    const occurrence = (seen.get(s.qname) || 0) + 1;
    seen.set(s.qname, occurrence);
    const usedModule = new Set();
    const usedRequire = new Set();
    const io = new Set();
    for (const r of scope.refs) {
      if (r.node.start < fn.start || r.node.end > fn.end) continue;
      if (r.scope === scope.root) {
        if (requireLocals.has(r.name)) {
          usedRequire.add(r.name);
          if (IO_MODULES.has(requireLocals.get(r.name))) io.add(`module:${requireLocals.get(r.name)}`);
        } else usedModule.add(r.name);
      } else if (!r.scope) {
        if (['setTimeout', 'setInterval', 'setImmediate', 'fetch'].includes(r.name)) io.add(r.name);
      }
    }
    const thisCalls = new Set();
    walk.simple(fn.body, {
      MemberExpression(m) {
        if (m.object.type === 'Identifier' && m.object.name === 'process' && !m.computed) {
          if (m.property.name === 'exit') io.add('process.exit');
          if (m.property.name === 'env') io.add('process.env');
        }
        if (m.object.type === 'Identifier' && m.object.name === 'Math' && m.property.name === 'random') io.add('Math.random');
        if (m.object.type === 'Identifier' && m.object.name === 'Date' && m.property.name === 'now') io.add('clock');
      },
      CallExpression(c) {
        if (c.callee.type === 'MemberExpression' && c.callee.object.type === 'ThisExpression' && !c.callee.computed) thisCalls.add(c.callee.property.name);
      },
      NewExpression(n) {
        if (n.callee.type === 'Identifier' && n.callee.name === 'Date' && n.arguments.length === 0) io.add('clock');
        if (n.callee.type === 'Identifier' && n.callee.name === 'Pool') io.add('new Pool');
      },
    });
    const sites = collectSites(src, fn);
    const mutants = pickMutants(sites, 3).map((m, i) => ({ ...m, id: `${s.qname}#${m.op}${i + 1}`, symbol: s.qname }));
    const rets = ownReturns(fn).filter((r) => r.argument);
    const params = fn.params.map((p) => {
      if (p.type === 'Identifier') return { kind: 'id', name: p.name, text: p.name };
      if (p.type === 'AssignmentPattern' && p.left.type === 'Identifier') return { kind: 'default', name: p.left.name, text: src.slice(p.start, p.end) };
      if (p.type === 'RestElement' && p.argument.type === 'Identifier') return { kind: 'rest', name: p.argument.name, text: src.slice(p.start, p.end) };
      return { kind: 'pattern', name: null, text: src.slice(p.start, p.end) };
    });
    const exported = s.className ? exportedNames.has(s.className) : exportedNames.has(s.name);
    return {
      qname: s.qname,
      occurrence,
      name: s.name,
      className: s.className,
      kind: s.kind,
      isStatic: s.isStatic,
      async: !!fn.async,
      generator: !!fn.generator,
      exported,
      private: s.name.startsWith('_') || s.name.startsWith('#'),
      start: s.decl.start,
      end: s.decl.end,
      line: s.decl.loc.start.line,
      endLine: s.decl.loc.end.line,
      loc: s.decl.loc.end.line - s.decl.loc.start.line + 1,
      chars: s.decl.end - s.decl.start,
      sha256: sha256(src.slice(s.decl.start, s.decl.end)),
      signature: signatureText(src, s),
      params,
      jsdoc: s.jsdoc,
      branchiness: branchiness(fn),
      returnsValue: rets.length > 0 || !!fn.expression,
      usedModule: [...usedModule].sort(),
      usedRequire: [...usedRequire].sort(),
      thisCalls: [...thisCalls].sort(),
      io: [...io].sort(),
      mutants,
    };
  });

  return {
    file: rel,
    chars: src.length,
    lines: src.split('\n').length,
    eol: eolOf(src),
    astral: /[\uD800-\uDBFF]/.test(src),
    sourceType: parsed.sourceType,
    requires: requires.filter((r) => r.spec).map((r) => ({ spec: r.spec, local: r.local, names: r.names, member: r.member, line: r.line })),
    requireStmts,
    exports: exp,
    bindings,
    symbols: syms,
  };
}

function main() {
  const a = args(process.argv.slice(2));
  const repoDir = path.resolve(a.base);
  let files;
  if (a.files) files = a.files.map((f) => path.join(repoDir, f));
  else {
    let roots = a.roots;
    if (!roots || !roots.length) roots = ['src', 'lib'].filter((r) => fs.existsSync(path.join(repoDir, r)));
    if (!roots.length) roots = ['.'];
    files = listJs(repoDir, roots);
  }
  let pkg = {};
  try { pkg = JSON.parse(fs.readFileSync(path.join(repoDir, 'package.json'), 'utf8')); } catch (_) { /* none */ }
  const out = { inventory: 1, package: pkg.name || null, files: files.map((f) => fileInfo(repoDir, f)) };
  process.stdout.write(JSON.stringify(out));
}

try { main(); } catch (e) { process.stderr.write(String(e && e.stack || e)); process.exit(2); }
