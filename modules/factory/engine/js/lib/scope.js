// Vendored from bench/js/lib/scope.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// Minimal lexical-scope resolver over an acorn ESTree. Finds identifier references
// that no enclosing scope declares ("free identifiers"). Used by the
// free-identifier-resolution gate: it catches invented helpers, undeclared
// variables and misspelt imports before anything is executed.
const walk = require('acorn-walk');

class Scope {
  constructor(parent, node) {
    this.parent = parent;
    this.node = node;
    this.names = new Set();
  }
  lookup(name) {
    for (let s = this; s; s = s.parent) if (s.names.has(name)) return s;
    return null;
  }
}

function declarePattern(p, scope) {
  if (!p) return;
  switch (p.type) {
    case 'Identifier': scope.names.add(p.name); break;
    case 'ObjectPattern':
      for (const prop of p.properties) declarePattern(prop.type === 'RestElement' ? prop.argument : prop.value, scope);
      break;
    case 'ArrayPattern': for (const el of p.elements) declarePattern(el, scope); break;
    case 'RestElement': declarePattern(p.argument, scope); break;
    case 'AssignmentPattern': declarePattern(p.left, scope); break;
    default: break; // MemberExpression targets declare nothing
  }
}

function declareStatement(stmt, scope) {
  if (!stmt) return;
  switch (stmt.type) {
    case 'VariableDeclaration':
      for (const d of stmt.declarations) declarePattern(d.id, scope);
      break;
    case 'FunctionDeclaration':
    case 'ClassDeclaration':
      if (stmt.id) scope.names.add(stmt.id.name);
      break;
    case 'ImportDeclaration':
      for (const s of stmt.specifiers) scope.names.add(s.local.name);
      break;
    case 'ExportNamedDeclaration':
    case 'ExportDefaultDeclaration':
      if (stmt.declaration) declareStatement(stmt.declaration, scope);
      break;
    default: break;
  }
}

function declareBlock(stmts, scope) {
  for (const s of stmts) declareStatement(s, scope);
}

// Hoist `var` (and sloppy-mode block function) declarations to the function scope.
function hoistVars(node, scope) {
  if (!node) return;
  switch (node.type) {
    case 'VariableDeclaration':
      if (node.kind === 'var') for (const d of node.declarations) declarePattern(d.id, scope);
      return;
    case 'FunctionDeclaration':
      if (node.id) scope.names.add(node.id.name);
      return; // do not enter nested functions
    case 'Program':
    case 'BlockStatement':
    case 'StaticBlock':
      for (const s of node.body) hoistVars(s, scope);
      return;
    case 'IfStatement': hoistVars(node.consequent, scope); hoistVars(node.alternate, scope); return;
    case 'ForStatement': hoistVars(node.init, scope); hoistVars(node.body, scope); return;
    case 'ForInStatement':
    case 'ForOfStatement': hoistVars(node.left, scope); hoistVars(node.body, scope); return;
    case 'WhileStatement':
    case 'DoWhileStatement':
    case 'LabeledStatement':
    case 'WithStatement': hoistVars(node.body, scope); return;
    case 'TryStatement':
      hoistVars(node.block, scope);
      if (node.handler) hoistVars(node.handler.body, scope);
      hoistVars(node.finalizer, scope);
      return;
    case 'SwitchStatement': for (const c of node.cases) for (const s of c.consequent) hoistVars(s, scope); return;
    case 'ExportNamedDeclaration':
    case 'ExportDefaultDeclaration': hoistVars(node.declaration, scope); return;
    default: return;
  }
}

/**
 * Resolve all identifier references in `ast`.
 * @param {any} ast Program node
 * @param {Set<string>} globals names that are implicitly available
 * @returns {{free: Map<string, any[]>, refs: Array<{name: string, node: any, scope: Scope|null}>, root: Scope}}
 */
function analyze(ast, globals = new Set()) {
  const root = new Scope(null, ast);
  hoistVars(ast, root);
  declareBlock(ast.body, root);
  const refs = [];
  const ref = (node, scope) => refs.push({ name: node.name, node, scope: scope.lookup(node.name) });
  const visitors = {
    Program(node, scope, c) { for (const s of node.body) c(s, scope, 'Statement'); },
    Function(node, scope, c) {
      const fs = new Scope(scope, node);
      if (node.type === 'FunctionExpression' && node.id) fs.names.add(node.id.name);
      if (node.type !== 'ArrowFunctionExpression') fs.names.add('arguments');
      for (const p of node.params) declarePattern(p, fs);
      for (const p of node.params) c(p, fs, 'Pattern');
      if (node.expression) c(node.body, fs, 'Expression');
      else { hoistVars(node.body, fs); c(node.body, fs, 'Statement'); }
    },
    BlockStatement(node, scope, c) {
      const bs = new Scope(scope, node);
      declareBlock(node.body, bs);
      for (const s of node.body) c(s, bs, 'Statement');
    },
    StaticBlock(node, scope, c) {
      const bs = new Scope(scope, node);
      hoistVars(node, bs);
      declareBlock(node.body, bs);
      for (const s of node.body) c(s, bs, 'Statement');
    },
    ForStatement(node, scope, c) {
      const s = new Scope(scope, node);
      if (node.init && node.init.type === 'VariableDeclaration') declareStatement(node.init, s);
      walk.base.ForStatement(node, s, c);
    },
    ForInStatement(node, scope, c) {
      const s = new Scope(scope, node);
      if (node.left.type === 'VariableDeclaration') declareStatement(node.left, s);
      walk.base.ForInStatement(node, s, c);
    },
    ForOfStatement(node, scope, c) {
      const s = new Scope(scope, node);
      if (node.left.type === 'VariableDeclaration') declareStatement(node.left, s);
      walk.base.ForOfStatement(node, s, c);
    },
    CatchClause(node, scope, c) {
      const s = new Scope(scope, node);
      if (node.param) declarePattern(node.param, s);
      walk.base.CatchClause(node, s, c);
    },
    SwitchStatement(node, scope, c) {
      const s = new Scope(scope, node);
      for (const cs of node.cases) declareBlock(cs.consequent, s);
      c(node.discriminant, scope, 'Expression');
      for (const cs of node.cases) {
        if (cs.test) c(cs.test, s, 'Expression');
        for (const st of cs.consequent) c(st, s, 'Statement');
      }
    },
    Class(node, scope, c) {
      const s = new Scope(scope, node);
      if (node.id) s.names.add(node.id.name);
      walk.base.Class(node, s, c);
    },
    Identifier(node, scope) { ref(node, scope); },
    VariablePattern(node, scope) { ref(node, scope); },
  };
  walk.recursive(ast, root, visitors);
  const free = new Map();
  for (const r of refs) {
    if (r.scope || globals.has(r.name)) continue;
    if (!free.has(r.name)) free.set(r.name, []);
    free.get(r.name).push(r.node);
  }
  return { free, refs, root };
}

module.exports = { analyze, Scope };
