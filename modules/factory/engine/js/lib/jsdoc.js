// Vendored from bench/js/lib/jsdoc.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// JSDoc block parsing (just enough for coverage checks) and splicing.

/** Read a balanced {...} type starting at s[i] === '{'. Returns [type, nextIndex] or null. */
function readBraced(s, i) {
  if (s[i] !== '{') return null;
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    if (s[j] === '{') depth++;
    else if (s[j] === '}') { depth--; if (depth === 0) return [s.slice(i + 1, j), j + 1]; }
  }
  return null;
}

/** Body text of a /** *\/ block with the leading stars removed. */
function blockBody(text) {
  return text
    .replace(/^\/\*\*/, '')
    .replace(/\*\/$/, '')
    .split('\n')
    .map((l) => l.replace(/^\s*\*( ?)/, ''))
    .join('\n');
}

/**
 * Parse tags from a JSDoc block.
 * @returns {{description: string, params: Array<{name: string, type: string|null, optional: boolean, sub: boolean}>, returns: Array<{type: string|null}>, tags: string[]}}
 */
function parseDoc(text) {
  const body = blockBody(text);
  const out = { description: '', params: [], returns: [], tags: [] };
  // split at tag starts (an @ at the beginning of a line)
  const parts = body.split(/\n(?=\s*@)/);
  out.description = parts[0].startsWith('@') ? '' : parts.shift().trim();
  for (const raw of parts) {
    const p = raw.trim();
    const m = /^@(\w+)/.exec(p);
    if (!m) continue;
    const tag = m[1];
    out.tags.push(tag);
    let i = m[0].length;
    while (p[i] === ' ' || p[i] === '\t') i++;
    let type = null;
    if (p[i] === '{') {
      const r = readBraced(p, i);
      if (r) { type = r[0].trim(); i = r[1]; }
    }
    while (p[i] === ' ' || p[i] === '\t') i++;
    if (tag === 'param' || tag === 'arg' || tag === 'argument') {
      let name = '';
      let optional = false;
      if (p[i] === '[') {
        const close = p.indexOf(']', i);
        name = p.slice(i + 1, close < 0 ? p.length : close);
        optional = true;
        name = name.split('=')[0].trim();
      } else {
        const mm = /^[^\s]+/.exec(p.slice(i));
        name = mm ? mm[0] : '';
      }
      name = name.replace(/^\.\.\./, '');
      out.params.push({ name, type, optional, sub: name.includes('.') || name.includes('[]') });
    } else if (tag === 'returns' || tag === 'return') {
      out.returns.push({ type });
    }
  }
  return out;
}

/** Re-indent a doc block to `indent` (lines become `indent * ...`). */
function reindent(doc, indent) {
  const lines = doc.trim().split(/\r?\n/);
  return lines.map((l, k) => {
    const t = l.trim();
    if (k === 0) return indent + t;
    if (t.startsWith('*')) return `${indent} ${t}`;
    return `${indent} * ${t}`;
  }).join('\n');
}

/**
 * Insert doc blocks before declarations. `inserts` = [{at: offset of decl start, doc}].
 * The block goes on its own line(s) directly above the declaration's line,
 * with the declaration line's indentation.
 */
function splice(src, inserts) {
  const sorted = [...inserts].sort((a, b) => b.at - a.at);
  let out = src;
  for (const ins of sorted) {
    const lineStart = out.lastIndexOf('\n', ins.at - 1) + 1;
    const indent = /^[ \t]*/.exec(out.slice(lineStart))[0];
    const prefix = out.slice(lineStart, ins.at);
    if (prefix.trim() !== '') throw new Error(`declaration does not start its line (offset ${ins.at})`);
    out = `${out.slice(0, lineStart)}${reindent(ins.doc, indent)}\n${out.slice(lineStart)}`;
  }
  return out;
}

module.exports = { parseDoc, splice, reindent, blockBody, readBraced };
