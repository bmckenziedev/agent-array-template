// Vendored from bench/js/lib/modinfo.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// CommonJS module facts: what it requires, what it exports, and its top-level bindings.
const fs = require('fs');
const path = require('path');
const { parse } = require('./parse');

function isRequireCall(n) {
  return n && n.type === 'CallExpression' && n.callee.type === 'Identifier' && n.callee.name === 'require' &&
    n.arguments.length === 1 && n.arguments[0].type === 'Literal' && typeof n.arguments[0].value === 'string';
}
function isModuleExports(n) {
  return n && n.type === 'MemberExpression' && !n.computed && n.object.type === 'Identifier' &&
    n.object.name === 'module' && n.property.name === 'exports';
}
function propName(key, computed) {
  if (computed) return key.type === 'Literal' ? String(key.value) : null;
  if (key.type === 'Identifier') return key.name;
  if (key.type === 'Literal') return String(key.value);
  return null;
}

/** Collect require() calls anywhere in the AST (static specifiers only). */
function collectRequires(ast) {
  const walk = require('acorn-walk');
  const out = [];
  walk.ancestor(ast, {
    CallExpression(node, _st, anc) {
      if (!isRequireCall(node)) {
        if (node.callee.type === 'Identifier' && node.callee.name === 'require') out.push({ spec: null, dynamic: true, node });
        return;
      }
      const parent = anc[anc.length - 2];
      const rec = { spec: node.arguments[0].value, node, names: [], local: null, member: null, line: node.loc.start.line };
      if (parent && parent.type === 'VariableDeclarator' && parent.init === node) {
        if (parent.id.type === 'Identifier') rec.local = parent.id.name;
        else if (parent.id.type === 'ObjectPattern') {
          for (const p of parent.id.properties) {
            if (p.type === 'Property') {
              const imported = propName(p.key, p.computed);
              const local = p.value.type === 'Identifier' ? p.value.name : (p.value.type === 'AssignmentPattern' && p.value.left.type === 'Identifier' ? p.value.left.name : null);
              rec.names.push({ imported, local });
            }
          }
        }
      } else if (parent && parent.type === 'MemberExpression' && parent.object === node && !parent.computed) {
        rec.member = parent.property.name;
      }
      out.push(rec);
    },
  });
  return out;
}

/** Static CommonJS export surface. */
function collectExports(ast) {
  const names = new Set();
  const spreads = [];
  let defaultIdent = null;
  let opaque = false;
  for (const st of ast.body) {
    if (st.type !== 'ExpressionStatement') continue;
    const e = st.expression;
    if (e.type === 'AssignmentExpression' && e.operator === '=') {
      const L = e.left;
      if (isModuleExports(L)) {
        if (e.right.type === 'ObjectExpression') {
          for (const p of e.right.properties) {
            if (p.type === 'SpreadElement') {
              if (isRequireCall(p.argument)) spreads.push(p.argument.arguments[0].value); else opaque = true;
            } else {
              const n = propName(p.key, p.computed);
              if (n) names.add(n); else opaque = true;
            }
          }
        } else if (e.right.type === 'Identifier') {
          defaultIdent = e.right.name;
        } else if (isRequireCall(e.right)) {
          spreads.push(e.right.arguments[0].value);
        } else opaque = true;
      } else if (L.type === 'MemberExpression' && !L.computed &&
        (isModuleExports(L.object) || (L.object.type === 'Identifier' && L.object.name === 'exports'))) {
        names.add(L.property.name);
      }
    } else if (e.type === 'CallExpression' && e.callee.type === 'MemberExpression' &&
      e.callee.object.type === 'Identifier' && e.callee.object.name === 'Object' &&
      e.callee.property.name === 'assign' && e.arguments[0] && isModuleExports(e.arguments[0])) {
      for (const a of e.arguments.slice(1)) {
        if (a.type === 'ObjectExpression') for (const p of a.properties) { const n = p.key && propName(p.key, p.computed); if (n) names.add(n); else opaque = true; }
        else opaque = true;
      }
    }
  }
  return { names: [...names], spreads, defaultIdent, opaque };
}

/** Resolve a relative require from `fromFile` to an existing file (node resolution subset). */
function resolveRelative(fromFile, spec) {
  const base = path.resolve(path.dirname(fromFile), spec);
  const cands = [base, `${base}.js`, `${base}.json`, `${base}.cjs`, path.join(base, 'index.js')];
  for (const c of cands) {
    try { if (fs.statSync(c).isFile()) return c; } catch (_) { /* next */ }
  }
  return null;
}

const cache = new Map();
/** Full export names of a file, following `...require('./x')` re-exports. */
function exportsOf(file, seen = new Set()) {
  if (cache.has(file)) return cache.get(file);
  if (seen.has(file)) return { names: [], opaque: true, defaultIdent: null };
  seen.add(file);
  let res;
  try {
    const src = fs.readFileSync(file, 'utf8');
    if (file.endsWith('.json')) {
      res = { names: Object.keys(JSON.parse(src)), opaque: false, defaultIdent: null };
    } else {
      const { ast } = parse(src, { sourceType: 'auto' });
      const ex = collectExports(ast);
      const names = new Set(ex.names);
      let opaque = ex.opaque;
      for (const s of ex.spreads) {
        const r = s.startsWith('.') ? resolveRelative(file, s) : null;
        if (!r) { opaque = true; continue; }
        const sub = exportsOf(r, seen);
        sub.names.forEach((n) => names.add(n));
        opaque = opaque || sub.opaque || !!sub.defaultIdent;
      }
      res = { names: [...names], opaque, defaultIdent: ex.defaultIdent };
    }
  } catch (e) {
    res = { names: [], opaque: true, defaultIdent: null, error: String(e.message || e) };
  }
  cache.set(file, res);
  return res;
}

module.exports = { collectRequires, collectExports, exportsOf, resolveRelative, isRequireCall, isModuleExports, propName };
