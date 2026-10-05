// Vendored from bench/js/lib/symbols.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// Function-like symbols of a module, addressed the way cards address them:
//   fn                 top-level function declaration / const fn = () => {}
//   Class.method       class method (static methods too; constructor = Class.constructor)
const walk = require('acorn-walk');

function isFn(n) {
  return !!n && (n.type === 'FunctionExpression' || n.type === 'ArrowFunctionExpression' || n.type === 'FunctionDeclaration');
}

/** The JSDoc block (/** ... *\/) that directly precedes `declStart`, or null. */
function leadingJsDoc(src, comments, declStart) {
  let best = null;
  for (const c of comments) {
    if (c.type !== 'Block' || c.end > declStart) continue;
    if (!best || c.end > best.end) best = c;
  }
  if (!best) return null;
  const gap = src.slice(best.end, declStart);
  if (!/^\s*$/.test(gap)) return null;
  if (!best.value.startsWith('*')) return null;
  return { start: best.start, end: best.end, text: src.slice(best.start, best.end) };
}

/**
 * List function-like symbols of a parsed module.
 * @returns {Array<{qname: string, name: string, className: string|null, kind: string, isStatic: boolean, fn: any, decl: any, jsdoc: any, classNode: any}>}
 */
function listSymbols(src, ast, comments) {
  const out = [];
  const push = (qname, name, className, kind, fn, decl, isStatic, classNode) => {
    out.push({ qname, name, className, kind, isStatic: !!isStatic, fn, decl, classNode: classNode || null, jsdoc: leadingJsDoc(src, comments, decl.start) });
  };
  const visitClass = (cls, cname) => {
    if (!cname) return;
    for (const m of cls.body.body) {
      if (m.type !== 'MethodDefinition' || m.computed || m.key.type !== 'Identifier') continue;
      if (m.kind === 'method' || m.kind === 'constructor') {
        push(`${cname}.${m.key.name}`, m.key.name, cname, m.kind === 'constructor' ? 'constructor' : 'method', m.value, m, m.static, cls);
      } else if (m.kind === 'get' || m.kind === 'set') {
        push(`${cname}.${m.kind}:${m.key.name}`, m.key.name, cname, m.kind, m.value, m, m.static, cls);
      }
    }
  };
  for (const st of ast.body) {
    if (st.type === 'FunctionDeclaration' && st.id) push(st.id.name, st.id.name, null, 'function', st, st);
    else if (st.type === 'ClassDeclaration' && st.id) visitClass(st, st.id.name);
    else if (st.type === 'VariableDeclaration') {
      for (const d of st.declarations) {
        if (d.id.type !== 'Identifier' || !d.init) continue;
        if (isFn(d.init)) push(d.id.name, d.id.name, null, d.init.type === 'ArrowFunctionExpression' ? 'arrow' : 'function', d.init, st.declarations.length === 1 ? st : d);
        else if (d.init.type === 'ClassExpression') visitClass(d.init, d.id.name);
      }
    } else if (st.type === 'ExpressionStatement' && st.expression.type === 'AssignmentExpression') {
      const L = st.expression.left;
      const R = st.expression.right;
      if (L.type === 'MemberExpression' && !L.computed && isFn(R)) {
        const obj = L.object;
        const isExp = (obj.type === 'Identifier' && obj.name === 'exports') ||
          (obj.type === 'MemberExpression' && obj.object.type === 'Identifier' && obj.object.name === 'module' && obj.property.name === 'exports');
        if (isExp) push(L.property.name, L.property.name, null, 'function', R, st);
      }
    }
  }
  return out;
}

/** Cyclomatic-style branch count of a function body (nested callbacks included). */
function branchiness(fn) {
  let n = 0;
  const inc = () => { n++; };
  walk.simple(fn.body, {
    IfStatement: inc,
    ConditionalExpression: inc,
    SwitchCase(c) { if (c.test) n++; },
    ForStatement: inc,
    ForInStatement: inc,
    ForOfStatement: inc,
    WhileStatement: inc,
    DoWhileStatement: inc,
    CatchClause: inc,
    LogicalExpression: inc,
    ChainExpression: inc,
  });
  return n;
}

/** Own (not nested-function) return statements of a function. */
function ownReturns(fn) {
  const out = [];
  if (fn.expression) return out;
  walk.recursive(fn.body, null, {
    Function() { /* do not descend into nested functions */ },
    ReturnStatement(node, st, c) { out.push(node); if (node.argument) c(node.argument, st, 'Expression'); },
  });
  return out;
}

function findSymbol(symbols, qname, occurrence = 1) {
  const hits = symbols.filter((s) => s.qname === qname);
  return hits[occurrence - 1] || null;
}

module.exports = { listSymbols, branchiness, ownReturns, findSymbol, leadingJsDoc, isFn };
