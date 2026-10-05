// Vendored from bench/js/lib/parse.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// Thin acorn wrapper. Every gate parses with the same options so that
// "parses" means the same thing everywhere.
const acorn = require('acorn');

const BASE = {
  ecmaVersion: 'latest',
  locations: true,
  ranges: true,
  allowHashBang: true,
  allowReturnOutsideFunction: false,
};

/**
 * Parse JS source. Tries CommonJS ("script") first, then ESM ("module"),
 * because babel-jest accepts both in test files.
 * @param {string} src
 * @param {{sourceType?: 'script'|'module'|'auto'}} [opts]
 * @returns {{ast: any, comments: any[], sourceType: string}}
 */
function parse(src, opts = {}) {
  const want = opts.sourceType || 'script';
  const order = want === 'auto' ? ['script', 'module'] : [want];
  let lastErr;
  for (const sourceType of order) {
    const comments = [];
    try {
      const ast = acorn.parse(src, { ...BASE, sourceType, onComment: comments });
      return { ast, comments, sourceType };
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

/** Token stream without comments, as [type, value] strings (for "only comments changed" checks). */
function tokens(src, sourceType = 'script') {
  const out = [];
  for (const t of acorn.tokenizer(src, { ...BASE, sourceType })) {
    out.push(`${t.type.label}:${t.value === undefined ? '' : String(t.value)}`);
  }
  return out;
}

module.exports = { parse, tokens, acorn };
