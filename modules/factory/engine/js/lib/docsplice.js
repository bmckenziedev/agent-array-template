'use strict';
// Doc-map checks and splicing shared by the doc_map gate and the bundle assembler.
// Comment-only edits: the code token stream must be identical before and after.
const crypto = require('crypto');
const { parse, tokens } = require('./parse');
const { listSymbols, findSymbol, ownReturns } = require('./symbols');
const { parseDoc, reindent } = require('./jsdoc');

const DOC_PLACEHOLDER = /\b(TODO|FIXME|TBD|XXX)\b|lorem ipsum|description here|\[description\]|<description>|insert (a )?description/i;
// Tags a per-symbol doc block may not carry: compiler directives and declarations that would
// widen or switch off type checking (that would game the tsc new-error gate) or add global types.
const FORBIDDEN_TAG = /@(ts-[a-z-]+|type\b|typedef\b|callback\b|enum\b|satisfies\b|overload\b|import\b)/i;
// Characters a doc block may not carry: C0 controls other than tab/LF/CR (a NUL makes git treat the
// file as binary), DEL, lone CR, Unicode line/paragraph separators, BOM, and bidi controls, which can
// make the code next to a comment read differently from what it is in review ("Trojan Source").
const BAD_CODES = [[0x00, 0x08], [0x0b, 0x0c], [0x0e, 0x1f], [0x7f, 0x7f], [0x61c, 0x61c], [0x200e, 0x200f],
  [0x2028, 0x2029], [0x202a, 0x202e], [0x2066, 0x2069], [0xfeff, 0xfeff]];

/** First disallowed code unit in `s` (a lone CR counts), or -1. */
function badChar(s) {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 13 && s.charCodeAt(i + 1) !== 10) return c;
    for (const [a, b] of BAD_CODES) if (c >= a && c <= b) return c;
  }
  return -1;
}
const ANY_TYPES = new Set(['*', 'any', '?', 'unknown']);

function sha256(s) { return crypto.createHash('sha256').update(s, 'utf8').digest('hex'); }

function eolOf(src) {
  const crlf = (src.match(/\r\n/g) || []).length;
  const lf = (src.match(/\n/g) || []).length - crlf;
  return crlf > lf ? '\r\n' : '\n';
}

/** Parse a source file and list its function-like symbols. Throws on a syntax error. */
function symbolsOf(src) {
  const { ast, comments, sourceType } = parse(src, { sourceType: 'auto' });
  return { syms: listSymbols(src, ast, comments), sourceType };
}

/** The packer's slice hash: sha256 over the per-symbol sha256 of each declaration text, joined by \n. */
function sliceSha(src, syms, qnames) {
  const parts = [];
  for (const q of qnames) {
    const s = findSymbol(syms, q, 1);
    if (!s) return null;
    parts.push(sha256(src.slice(s.decl.start, s.decl.end)));
  }
  return sha256(parts.join('\n'));
}

function formalParams(fn) {
  return fn.params.map((p) => {
    if (p.type === 'Identifier') return p.name;
    if (p.type === 'AssignmentPattern' && p.left.type === 'Identifier') return p.left.name;
    if (p.type === 'RestElement' && p.argument.type === 'Identifier') return p.argument.name;
    return null; // destructured: any name is accepted
  });
}

/** Coverage problems of one doc block for one symbol (empty list = ok). */
function coverage(sym, doc) {
  const problems = [];
  const d = parseDoc(doc);
  const formals = formalParams(sym.fn);
  const top = d.params.filter((p) => !p.sub);
  for (const p of d.params) if (!p.type) problems.push(`@param ${p.name || '(unnamed)'} has no {type}`);
  if (top.length !== formals.length) {
    problems.push(`${formals.length} formal parameter(s) (${formals.map((f) => f || '{…}').join(', ') || 'none'}) but ${top.length} top-level @param tag(s)`);
  } else {
    formals.forEach((f, i) => { if (f && top[i].name !== f) problems.push(`@param #${i + 1} is named \`${top[i].name}\` but the parameter is \`${f}\``); });
  }
  const isCtor = sym.kind === 'constructor';
  const valueReturn = ownReturns(sym.fn).some((r) => r.argument) || !!sym.fn.expression;
  const needsReturns = !isCtor && (sym.fn.async || valueReturn);
  if (needsReturns && !d.returns.length) problems.push(sym.fn.async ? 'missing @returns {Promise<…>} (async function)' : 'missing @returns (the function returns a value)');
  for (const r of d.returns) if (!r.type) problems.push('@returns has no {type}');
  if (isCtor && d.returns.length) problems.push('a constructor must not have @returns');
  if (!/[A-Za-z]{2}/.test(d.description) && !d.tags.includes('description')) problems.push('no description line');
  return problems;
}

/** Type tags of a doc block, for the type-strength floor. */
function typeTags(doc) {
  const d = parseDoc(doc);
  return [...d.params.map((p) => p.type), ...d.returns.map((r) => r.type)].filter((t) => t !== null);
}

/** Validate the model's JSON doc map against the wanted symbol list. Returns {ok, stage, message, map}. */
function checkMap(code, want, opts = {}) {
  let map;
  try { map = JSON.parse(String(code).trim()); } catch (e) { return { ok: false, stage: 'parse', message: `output is not valid JSON: ${e.message}` }; }
  if (!map || typeof map !== 'object' || Array.isArray(map)) return { ok: false, stage: 'parse', message: 'output must be a JSON object {"symbol": "/** ... */"}' };
  const keys = Object.keys(map);
  const extra = keys.filter((k) => !want.includes(k));
  const missing = want.filter((k) => !keys.includes(k));
  if (extra.length) return { ok: false, stage: 'scope', message: `unknown symbol key(s): ${extra.join(', ')}; document exactly: ${want.join(', ')}` };
  if (missing.length) return { ok: false, stage: 'doc_coverage', message: `missing doc block(s) for: ${missing.join(', ')}` };
  const hyg = [];
  for (const k of keys) {
    const v = map[k];
    if (typeof v !== 'string') { hyg.push(`${k}: value must be a string`); continue; }
    const t = v.trim();
    if (!t.startsWith('/**') || !t.endsWith('*/')) hyg.push(`${k}: value must be one /** ... */ block`);
    else if (t.slice(3, -2).includes('*/')) hyg.push(`${k}: the block contains "*/" before its end`);
    if (DOC_PLACEHOLDER.test(t)) hyg.push(`${k}: placeholder text`);
    const bc = badChar(v);
    if (bc >= 0) hyg.push(`${k}: control, bidi or line-separator character U+${bc.toString(16).toUpperCase().padStart(4, '0')} is not allowed`);
    const ft = FORBIDDEN_TAG.exec(t);
    if (ft) hyg.push(`${k}: tag @${ft[1]} is not allowed in a doc_map block`);
    if (t.length > (opts.maxBlockChars || 4000)) hyg.push(`${k}: block longer than ${opts.maxBlockChars || 4000} chars`);
  }
  if (hyg.length) return { ok: false, stage: 'hygiene', message: hyg.join('; ') };
  return { ok: true, map };
}

/**
 * Insert doc blocks above declarations. `inserts` = [{at: offset of decl start, doc}].
 * The block goes on its own line(s) directly above the declaration's line, indented like it,
 * using the file's dominant line ending.
 */
function splice(src, inserts) {
  const eol = eolOf(src);
  const sorted = [...inserts].sort((a, b) => b.at - a.at);
  let out = src;
  for (const ins of sorted) {
    const lineStart = out.lastIndexOf('\n', ins.at - 1) + 1;
    const indent = /^[ \t]*/.exec(out.slice(lineStart))[0];
    const prefix = out.slice(lineStart, ins.at);
    if (prefix.trim() !== '') throw new Error(`declaration does not start its line (offset ${ins.at})`);
    const block = reindent(ins.doc, indent).split('\n').join(eol);
    out = `${out.slice(0, lineStart)}${block}${eol}${out.slice(lineStart)}`;
  }
  return out;
}

/**
 * Apply one doc map to a source: locate symbols, refuse documented ones, splice, re-parse,
 * require an identical code-token stream, check coverage.
 * Returns {ok, stage, message, spliced, coverage[]}.
 */
function applyDocMap(src, want, map, opts = {}) {
  let syms, sourceType;
  try { ({ syms, sourceType } = symbolsOf(src)); } catch (e) { return { ok: false, stage: 'target', message: `target file does not parse: ${e.message}` }; }
  const inserts = [];
  const cov = [];
  for (const k of want) {
    const s = findSymbol(syms, k, 1);
    if (!s) return { ok: false, stage: 'target', message: `target ${k} not found (snapshot drift)` };
    if (s.jsdoc && !opts.allowDocumented) return { ok: false, stage: 'target', message: `target ${k} already has a doc block` };
    inserts.push({ at: s.decl.start, doc: map[k].trim() });
    for (const p of coverage(s, map[k])) cov.push(`${k}: ${p}`);
  }
  let spliced;
  try { spliced = splice(src, inserts); } catch (e) { return { ok: false, stage: 'splice', message: String(e.message) }; }
  try { parse(spliced, { sourceType }); } catch (e) { return { ok: false, stage: 'splice', message: `file no longer parses after splicing: ${e.message}` }; }
  const ta = tokens(src, sourceType);
  const tb = tokens(spliced, sourceType);
  if (ta.length !== tb.length || ta.some((x, i) => x !== tb[i])) return { ok: false, stage: 'splice', message: 'splicing changed code tokens (doc blocks may contain only comments)' };
  if (cov.length) return { ok: false, stage: 'doc_coverage', message: cov.slice(0, 10).join('; '), detail: cov, spliced };
  return { ok: true, stage: 'spliced', spliced };
}

/** Fraction of type tags that are any-like (*, any, ?, unknown). */
function anyFraction(map) {
  let n = 0;
  let anyN = 0;
  for (const v of Object.values(map)) {
    for (const t of typeTags(v)) { n++; if (ANY_TYPES.has(t.trim())) anyN++; }
  }
  return n ? anyN / n : 0;
}

module.exports = { badChar, checkMap, applyDocMap, splice, sliceSha, coverage, anyFraction, eolOf, symbolsOf, sha256 };
