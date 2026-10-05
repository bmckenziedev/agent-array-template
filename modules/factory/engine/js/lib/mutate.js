// Vendored from bench/js/lib/mutate.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// Semantic mutants of one target function. Four operators, as in the corrected plan:
//   ret   return-value swap   (the function's main return value replaced by a wrong one)
//   cond  conditional flip    (if / ?: test negated)
//   bound boundary off-by-one (< <-> <=, > <-> >=, === N -> === N+1)
//   call  dropped call        (a call statement removed; logging calls are never dropped)
// Mutants are text splices on the original source, so they are reproducible from the
// card alone (start/end/original/replacement are stored in the card and re-checked).
const walk = require('acorn-walk');
const { ownReturns } = require('./symbols');

const LOG_ROOTS = new Set(['console', 'logger', 'log', 'debug']);

function calleeParts(callee) {
  let n = callee;
  const parts = [];
  while (n) {
    if (n.type === 'MemberExpression') { if (!n.computed && n.property.name) parts.unshift(n.property.name); n = n.object; }
    else if (n.type === 'ChainExpression') n = n.expression;
    else if (n.type === 'Identifier') { parts.unshift(n.name); break; }
    else if (n.type === 'ThisExpression') { parts.unshift('this'); break; }
    else break;
  }
  return parts;
}

function isLoggingCall(call) {
  const parts = calleeParts(call.callee);
  if (!parts.length) return false;
  if (LOG_ROOTS.has(parts[0])) return true;
  if (parts[0] === 'this' && parts[1] && (LOG_ROOTS.has(parts[1]) || /^log/i.test(parts[1]))) return true;
  if (parts[0] === 'process' && (parts[1] === 'stdout' || parts[1] === 'stderr')) return true;
  return false;
}

function retReplacement(src, arg) {
  const text = src.slice(arg.start, arg.end);
  if (arg.type === 'Literal') {
    if (typeof arg.value === 'boolean') return String(!arg.value);
    if (typeof arg.value === 'number') return String(arg.value + 1);
    if (typeof arg.value === 'string') return arg.value.length ? "''" : "'__mutant__'";
    if (arg.value === null) return 'undefined';
  }
  if (arg.type === 'Identifier' && arg.name === 'undefined') return 'null';
  if (arg.type === 'ArrayExpression') return arg.elements.length ? '[]' : 'null';
  if (arg.type === 'ObjectExpression') return arg.properties.length ? '{}' : 'null';
  if (arg.type === 'UnaryExpression' && arg.operator === '!') return `!(${text})`;
  if (arg.type === 'BinaryExpression' && ['===', '!==', '==', '!=', '<', '<=', '>', '>='].includes(arg.operator)) return `!(${text})`;
  if (arg.type === 'LogicalExpression' && (arg.operator === '&&' || arg.operator === '||')) return `!(${text})`;
  return 'null';
}

const FLIP = { '<': '<=', '<=': '<', '>': '>=', '>=': '>' };

/**
 * Collect every mutation site of each operator inside `fn`.
 * @returns {{ret: any[], cond: any[], bound: any[], call: any[]}}
 */
function collectSites(src, fn) {
  const sites = { ret: [], cond: [], bound: [], call: [] };
  const mk = (op, node, start, end, replacement) => ({
    op, start, end, line: node.loc.start.line, original: src.slice(start, end), replacement,
  });
  if (fn.expression) {
    sites.ret.push(mk('ret', fn.body, fn.body.start, fn.body.end, retReplacement(src, fn.body)));
  } else {
    // own returns, last one first (the main result rather than an early exit)
    const rets = ownReturns(fn).filter((r) => r.argument).reverse();
    for (const r of rets) {
      const rep = retReplacement(src, r.argument);
      if (rep !== src.slice(r.argument.start, r.argument.end)) sites.ret.push(mk('ret', r.argument, r.argument.start, r.argument.end, rep));
    }
  }
  walk.simple(fn.body, {
    IfStatement(n) { sites.cond.push(mk('cond', n.test, n.test.start, n.test.end, `!(${src.slice(n.test.start, n.test.end)})`)); },
    ConditionalExpression(n) { sites.cond.push(mk('cond', n.test, n.test.start, n.test.end, `!(${src.slice(n.test.start, n.test.end)})`)); },
    BinaryExpression(n) {
      if (FLIP[n.operator]) {
        const between = src.slice(n.left.end, n.right.start);
        const idx = between.indexOf(n.operator);
        if (idx >= 0) {
          const s = n.left.end + idx;
          sites.bound.push(mk('bound', n, s, s + n.operator.length, FLIP[n.operator]));
        }
      } else if (['===', '!==', '==', '!='].includes(n.operator)) {
        const lit = [n.left, n.right].find((x) => x.type === 'Literal' && typeof x.value === 'number');
        if (lit) sites.bound.push(mk('bound', lit, lit.start, lit.end, String(lit.value + 1)));
      }
    },
    ExpressionStatement(n) {
      let e = n.expression;
      if (e.type === 'AwaitExpression') e = e.argument;
      if (e.type === 'ChainExpression') e = e.expression;
      if (!e || e.type !== 'CallExpression' || e.callee.type === 'Super') return;
      if (isLoggingCall(e)) return;
      sites.call.push(mk('call', n, n.start, n.end, 'void 0;'));
    },
  });
  for (const k of ['cond', 'bound', 'call']) sites[k].sort((a, b) => a.start - b.start);
  return sites;
}

const PRIORITY = ['ret', 'cond', 'bound', 'call'];

/** Up to n mutants: one per operator in priority order, then further sites round-robin. */
function pickMutants(sites, n = 3) {
  const chosen = [];
  const used = new Set();
  const key = (s) => `${s.start}:${s.end}`;
  for (const op of PRIORITY) {
    if (chosen.length >= n) break;
    const s = sites[op].find((x) => !used.has(key(x)));
    if (s) { chosen.push(s); used.add(key(s)); }
  }
  for (let round = 0; chosen.length < n && round < 50; round++) {
    let added = false;
    for (const op of PRIORITY) {
      if (chosen.length >= n) break;
      const c = sites[op].find((x) => !used.has(key(x)));
      if (c) { chosen.push(c); used.add(key(c)); added = true; }
    }
    if (!added) break;
  }
  return chosen;
}

function applyMutant(src, m) {
  if (src.slice(m.start, m.end) !== m.original) throw new Error(`mutant ${m.id} does not match the snapshot (slice changed)`);
  return src.slice(0, m.start) + m.replacement + src.slice(m.end);
}

module.exports = { collectSites, pickMutants, applyMutant, PRIORITY, isLoggingCall };
