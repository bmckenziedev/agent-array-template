'use strict';
// Factory gate runner. One JSON request in, one JSON result out.
//
//   node gate.js --base <repoDir> [--tools <dir with node_modules>] [--scratch <dir>]
//                [--request <file>] [--sentinel]
//
// The request comes from --request <file> or stdin. With --sentinel the result is printed as
// one line `@@FACTORY-GATE-RESULT@@<json>` so it survives being mixed into a tester's output
// tail (pipeline/job/tester.py returns stdout+stderr).
//
// kinds:
//   doc_map       JSON doc map -> keys/hygiene -> splice (comment-only) -> coverage ->
//                 type-strength floor -> tsc --checkJs new-error delta 0 vs the cached baseline
//   tsc_baseline  compute + cache the tsc diagnostics of the untouched base
//   tsc_check     tsc delta of an overlay {relpath: content} (bundle integration gate)
//   assemble_docs apply several accepted doc maps to one file in order (bundle assembler)
//   test_gen      static gates -> jest --json (min_tests, all pass) -> semantic mutants.
//                 EXECUTES model output: refused unless FACTORY_GATE_ISOLATED=1 (set only in the
//                 offline gate container / tester sidecar image).
//   version       tool versions
//
// The base repo is never written: every check runs in a fresh copy under --scratch.
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

// The pinned tools (acorn, typescript) live in <--tools>/node_modules. A tester sidecar runs this
// script with a minimal env (no NODE_PATH), so put that dir on the module path before any require.
(function bootstrapToolsPath() {
  const i = process.argv.indexOf('--tools');
  const nm = path.resolve(i > 0 ? process.argv[i + 1] : __dirname, 'node_modules');
  const cur = (process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean);
  if (!cur.includes(nm)) {
    process.env.NODE_PATH = [nm, ...cur].join(path.delimiter);
    require('module').Module._initPaths();
  }
}());

const { checkMap, applyDocMap, sliceSha, anyFraction, symbolsOf } = require('./lib/docsplice');

const SENTINEL = '@@FACTORY-GATE-RESULT@@';
const COPY_SKIP = new Set(['node_modules', '.git', 'coverage', '.factory', '.aa']);

function parseArgs(argv) {
  const a = { base: null, deps: null, tools: path.resolve(__dirname), scratch: path.join(os.tmpdir(), 'factory-gate'), request: null, sentinel: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--base') a.base = path.resolve(argv[++i]);
    else if (k === '--tools') a.tools = path.resolve(argv[++i]);
    else if (k === '--deps') a.deps = path.resolve(argv[++i]);
    else if (k === '--scratch') a.scratch = path.resolve(argv[++i]);
    else if (k === '--request') a.request = path.resolve(argv[++i]);
    else if (k === '--sentinel') a.sentinel = true;
  }
  return a;
}

function readStdin() {
  return new Promise((resolve) => {
    const chunks = [];
    process.stdin.on('data', (c) => chunks.push(c));
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
  });
}

function rid() { return `${Date.now().toString(36)}-${process.pid}-${crypto.randomBytes(3).toString('hex')}`; }
function rmrf(p) { try { fs.rmSync(p, { recursive: true, force: true }); } catch (_) { /* ignore */ } }
function sha256(s) { return crypto.createHash('sha256').update(s).digest('hex'); }

/** Repo-relative path, validated: no absolute paths, no .., no .git. */
function safeRel(rel) {
  const r = String(rel || '').replace(/\\/g, '/');
  if (!r || r.startsWith('/') || /^[A-Za-z]:/.test(r)) throw new Error(`unsafe path ${rel}`);
  const parts = r.split('/');
  if (parts.some((p) => p === '..' || p === '' || p.toLowerCase() === '.git')) throw new Error(`unsafe path ${rel}`);
  return parts.join(path.sep);
}

function run(cmd, args, { cwd, env, timeoutMs }) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const child = spawn(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => { if (out.length < 2000000) out += d; });
    child.stderr.on('data', (d) => { if (err.length < 400000) err += d; });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try { if (process.platform === 'win32') child.kill('SIGKILL'); else child.kill('SIGKILL'); } catch (_) { /* gone */ }
    }, timeoutMs);
    child.on('error', (e) => { err += String(e); });
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, timedOut, out, err, ms: Date.now() - t0 });
    });
  });
}

/** node_modules for runs: the repo's own (shipped with the snapshot), else the --deps dir (a deps cache
 *  built from the real lockfile: the design's deps image), else none. Always linked, never copied. */
function depsDir(ctx) {
  const own = path.join(ctx.base, 'node_modules');
  if (fs.existsSync(own)) return own;
  if (ctx.deps && fs.existsSync(ctx.deps)) return ctx.deps;
  return null;
}

function linkDeps(ctx, dir) {
  const nm = depsDir(ctx);
  if (nm) fs.symlinkSync(nm, path.join(dir, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
}

/** Copy the base repo (without node_modules/.git) to a fresh run dir; link node_modules back. */
function makeRunDir(ctx, tag) {
  const dir = path.join(ctx.scratch, `run-${tag}-${rid()}`);
  fs.mkdirSync(dir, { recursive: true });
  fs.cpSync(ctx.base, dir, { recursive: true, filter: (s) => !COPY_SKIP.has(path.basename(s)) || s === ctx.base });
  linkDeps(ctx, dir);
  return dir;
}

function cloneRunDir(ctx, src, tag) {
  const dir = path.join(ctx.scratch, `run-${tag}-${rid()}`);
  fs.mkdirSync(dir, { recursive: true });
  fs.cpSync(src, dir, { recursive: true, filter: (s) => path.basename(s) !== 'node_modules' || s === src });
  linkDeps(ctx, dir);
  return dir;
}

// ---------------------------------------------------------------- tsc
function tscConfig(ctx, profile) {
  const t = (profile && profile.tsc) || {};
  const include = (t.include || ['src/**/*.js']).map((g) => `../${g}`);
  const exclude = (t.exclude || ['**/__tests__/**', '**/*.test.js', '**/*.spec.js']).map((g) => `../${g}`);
  exclude.push('../node_modules');
  const compilerOptions = Object.assign({
    allowJs: true, checkJs: true, noEmit: true, target: 'es2022', lib: ['es2023'],
    module: 'nodenext', moduleResolution: 'nodenext', strict: false, skipLibCheck: true,
    resolveJsonModule: true, maxNodeModuleJsDepth: 0, types: ['node'],
    typeRoots: [path.join(ctx.tools, 'node_modules', '@types')], pretty: false,
  }, t.compilerOptions || {});
  return { compilerOptions, include, exclude };
}

function parseTsc(text) {
  const diags = [];
  for (const line of text.split(/\r?\n/)) {
    const m = /^(.+?)\((\d+),(\d+)\): error (TS\d+): (.*)$/.exec(line);
    if (m) diags.push({ file: m[1], line: Number(m[2]), col: Number(m[3]), code: m[4], message: m[5] });
    else {
      const g = /^error (TS\d+): (.*)$/.exec(line);
      if (g) diags.push({ file: '', line: 0, col: 0, code: g[1], message: g[2] });
    }
  }
  return diags;
}

function normPaths(text, dir) {
  let t = text;
  for (const v of [dir, dir.split(path.sep).join('/'), dir.split('/').join('\\')]) t = t.split(v + path.sep).join('').split(v + '/').join('').split(v).join('');
  return t.split('\\').join('/').replace(/^\.\//gm, '');
}

async function runTsc(ctx, dir, profile) {
  const fdir = path.join(dir, '.factory');
  fs.mkdirSync(fdir, { recursive: true });
  fs.writeFileSync(path.join(fdir, 'tsconfig.json'), JSON.stringify(tscConfig(ctx, profile), null, 2));
  const tsc = path.join(ctx.tools, 'node_modules', 'typescript', 'bin', 'tsc');
  const timeoutS = ((profile && profile.tsc) || {}).timeoutS || 90;
  const r = await run(process.execPath, [tsc, '-p', path.join(fdir, 'tsconfig.json')],
    { cwd: dir, env: { PATH: process.env.PATH, HOME: ctx.scratch, SystemRoot: process.env.SystemRoot || '' }, timeoutMs: timeoutS * 1000 });
  const text = normPaths(`${r.out}\n${r.err}`, dir);
  const diags = parseTsc(text);
  const crashed = !r.timedOut && r.code !== 0 && diags.length === 0;
  return { ms: r.ms, timedOut: r.timedOut, code: r.code, crashed, diags, raw: text.slice(0, 2000) };
}

const keyOf = (d) => `${d.file}|${d.code}|${d.message}`;

function multisetDelta(after, before) {
  const counts = new Map();
  for (const d of before) counts.set(keyOf(d), (counts.get(keyOf(d)) || 0) + 1);
  const added = [];
  for (const d of after) {
    const k = keyOf(d);
    const c = counts.get(k) || 0;
    if (c > 0) counts.set(k, c - 1); else added.push(d);
  }
  return added;
}

async function tscBaseline(ctx, profile) {
  const key = sha256(JSON.stringify([ctx.base, depsDir(ctx), tscConfig(ctx, profile), toolVersions(ctx)])).slice(0, 16);
  const cache = path.join(ctx.scratch, `tsc-baseline-${key}.json`);
  if (fs.existsSync(cache)) {
    try { return JSON.parse(fs.readFileSync(cache, 'utf8')); } catch (_) { /* recompute */ }
  }
  const dir = makeRunDir(ctx, 'tsc-base');
  try {
    const r = await runTsc(ctx, dir, profile);
    if (r.timedOut) throw new Error('tsc baseline timed out');
    if (r.crashed) throw new Error(`tsc baseline crashed (exit ${r.code}): ${r.raw.slice(0, 600)}`);
    const b = { ms: r.ms, count: r.diags.length, diags: r.diags };
    fs.writeFileSync(`${cache}.${process.pid}.tmp`, JSON.stringify(b));
    fs.renameSync(`${cache}.${process.pid}.tmp`, cache);
    return b;
  } finally { rmrf(dir); }
}

/** tsc on base + overlay; returns {added, removed, after, baseline, ms}. */
async function tscDelta(ctx, profile, overlay, tag) {
  const baseline = await tscBaseline(ctx, profile);
  const dir = makeRunDir(ctx, tag);
  try {
    for (const [rel, content] of Object.entries(overlay)) {
      const abs = path.join(dir, safeRel(rel));
      fs.mkdirSync(path.dirname(abs), { recursive: true });
      fs.writeFileSync(abs, content);
    }
    const r = await runTsc(ctx, dir, profile);
    if (r.timedOut) return { error: 'tsc timed out', ms: r.ms };
    if (r.crashed) return { error: `tsc crashed (exit ${r.code}): ${r.raw.slice(0, 600)}`, ms: r.ms };
    return { added: multisetDelta(r.diags, baseline.diags), removed: multisetDelta(baseline.diags, r.diags), after: r.diags.length, baseline: baseline.count, ms: r.ms };
  } finally { rmrf(dir); }
}

let _versions = null;
function toolVersions(ctx) {
  if (_versions) return _versions;
  const v = { node: process.version };
  for (const p of ['typescript', 'acorn', 'acorn-walk', '@types/node']) {
    try { v[p] = JSON.parse(fs.readFileSync(path.join(ctx.tools, 'node_modules', p, 'package.json'), 'utf8')).version; } catch (_) { v[p] = null; }
  }
  _versions = v;
  return v;
}

// ---------------------------------------------------------------- doc_map
async function gateDocMap(ctx, req) {
  const card = req.unit;
  const profile = req.profile || {};
  const docs = profile.docs || {};
  const out = { kind: 'doc_map', unit_id: card.unit_id, checks: {}, timings: {} };
  const t0 = Date.now();
  const fail = (stage, message, detail) => Object.assign(out, { ok: false, stage, message, detail, timings: Object.assign(out.timings, { total_ms: Date.now() - t0 }) });
  const want = card.target.symbols;
  const m = checkMap(req.code, want, { maxBlockChars: docs.maxBlockChars });
  if (!m.ok) return fail(m.stage, m.message);
  out.checks.envelope_json = { ok: true };

  const rel = safeRel(card.target.file);
  const src = fs.readFileSync(path.join(ctx.base, rel), 'utf8');
  if (card.target.slice_sha256) {
    let syms;
    try { ({ syms } = symbolsOf(src)); } catch (e) { return fail('target', `target file does not parse: ${e.message}`); }
    const sha = sliceSha(src, syms, want);
    if (sha !== card.target.slice_sha256) return fail('target', 'target slice changed since packing (re-pack the card)');
  }
  const a = applyDocMap(src, want, m.map);
  if (!a.ok) return fail(a.stage, a.message, a.detail);
  out.checks.splice = { ok: true };
  out.checks.doc_coverage = { ok: true };

  const maxAny = docs.maxAnyFraction === undefined ? 0.8 : Number(docs.maxAnyFraction);
  const frac = anyFraction(m.map);
  out.checks.type_strength = { any_fraction: Math.round(frac * 100) / 100, max: maxAny };
  if (frac > maxAny) return fail('type_strength', `${Math.round(frac * 100)}% of the documented types are any-like (*, any, ?); use concrete types from the code and its call sites`);

  if (!(req.options && req.options.skipTsc)) {
    const d = await tscDelta(ctx, profile, { [card.target.file]: a.spliced }, card.unit_id);
    out.timings.tsc_ms = d.ms;
    if (d.error) return fail('gate_env', d.error);
    out.tsc = { baseline: d.baseline, after: d.after, new: d.added.length, fixed: d.removed.length, ms: d.ms };
    if (d.added.length) {
      const lines = a.spliced.split(/\r?\n/);
      const msg = d.added.slice(0, 6).map((x) => `${x.file}(${x.line},${x.col}) ${x.code}: ${x.message}` +
        (x.file === card.target.file && lines[x.line - 1] ? `  [line: ${lines[x.line - 1].trim().slice(0, 90)}]` : '')).join('\n');
      return fail('tsc', `tsc --checkJs reports ${d.added.length} new error(s):\n${msg}`, d.added.slice(0, 20));
    }
    out.checks.tsc = { ok: true, new: 0, fixed: d.removed.length };
  }
  if (req.options && req.options.returnSpliced) out.spliced = a.spliced;
  out.timings.total_ms = Date.now() - t0;
  return Object.assign(out, { ok: true, stage: 'pass' });
}

/** Apply accepted doc maps to one file in order. Each unit is re-located on the current text. */
async function assembleDocs(ctx, req) {
  const rel = safeRel(req.file);
  let src = req.source !== undefined ? req.source : fs.readFileSync(path.join(ctx.base, rel), 'utf8');
  const applied = [];
  const failed = [];
  for (const u of req.units) {
    const r = applyDocMap(src, u.symbols, u.map);
    if (r.ok) { src = r.spliced; applied.push(u.unit_id); } else failed.push({ unit_id: u.unit_id, stage: r.stage, message: r.message });
  }
  return { kind: 'assemble_docs', ok: failed.length === 0, file: req.file, applied, failed, spliced: src };
}

// ---------------------------------------------------------------- test_gen
function writeJestProfile(dir, profile) {
  const fdir = path.join(dir, '.factory');
  fs.mkdirSync(fdir, { recursive: true });
  const j = profile.jest || {};
  for (const [f, content] of Object.entries(j.files || {})) {
    const dst = path.join(fdir, safeRel(f));
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.writeFileSync(dst, content);
  }
  const mapper = {};
  for (const [k, v] of Object.entries(j.moduleNameMapper || {})) mapper[k] = v.replace('<factory>', fdir);
  const baseCfg = path.join(dir, j.baseJestConfig || 'jest.config.js');
  const cfg = `'use strict';
// generated by the factory gate: repo jest config + the per-repo test profile
let base = {};
try { base = require(${JSON.stringify(baseCfg)}); } catch (_) { base = {}; }
module.exports = Object.assign({}, base, {
  rootDir: ${JSON.stringify(dir)},
  setupFiles: [...(base.setupFiles || []), ...${JSON.stringify((j.setupFiles || []).map((f) => path.join(fdir, f)))}],
  moduleNameMapper: Object.assign({}, base.moduleNameMapper || {}, ${JSON.stringify(mapper)}),
  testTimeout: ${Number(j.testTimeoutMs || 10000)},
  cacheDirectory: ${JSON.stringify(path.join(os.tmpdir(), 'factory-jest-cache'))},
  watchman: false,
  collectCoverage: false,
});
`;
  fs.writeFileSync(path.join(fdir, 'jest.config.js'), cfg);
  return path.join(fdir, 'jest.config.js');
}

async function runJest(dir, profile, testPath) {
  const j = profile.jest || {};
  const cfg = writeJestProfile(dir, profile);
  const outFile = path.join(dir, '.factory', `jest-${rid()}.json`);
  const env = Object.assign({ PATH: process.env.PATH, HOME: os.tmpdir(), TZ: 'UTC', LANG: 'C.UTF-8', NODE_ENV: 'test', CI: '1' }, j.env || {});
  const jestBin = path.join(dir, 'node_modules', 'jest', 'bin', 'jest.js');
  if (!fs.existsSync(jestBin)) return { status: 'env', message: 'jest is not installed in the repo (node_modules/jest missing): TEST_ENV' };
  const args = [jestBin, '--config', cfg, '--ci', '--runInBand', '--json', '--outputFile', outFile,
    '--forceExit', '--silent', '--testTimeout', String(j.testTimeoutMs || 10000), '--runTestsByPath', testPath];
  const r = await run(process.execPath, args, { cwd: dir, env, timeoutMs: (j.timeoutS || 60) * 1000 });
  const res = { wallMs: r.ms, timedOut: r.timedOut, exitCode: r.code };
  if (r.timedOut) return Object.assign(res, { status: 'timeout' });
  let rep = null;
  try { rep = JSON.parse(fs.readFileSync(outFile, 'utf8')); } catch (_) { /* crashed */ }
  if (!rep) return Object.assign(res, { status: 'crash', stderr: (r.err || r.out).slice(-1500) });
  const tr = (rep.testResults || [])[0] || {};
  const failures = [];
  for (const a of tr.assertionResults || []) {
    if (a.status === 'failed') failures.push({ title: a.fullName || a.title, message: (a.failureMessages || []).join('\n').slice(0, 1200) });
  }
  Object.assign(res, {
    numTotalTests: rep.numTotalTests, numPassedTests: rep.numPassedTests, numFailedTests: rep.numFailedTests,
    numPendingTests: rep.numPendingTests, numTodoTests: rep.numTodoTests, success: rep.success,
    suiteStatus: tr.status, suiteMessage: tr.status === 'failed' && !(tr.assertionResults || []).length ? String(tr.message || '').slice(0, 1500) : undefined,
    failures,
  });
  res.status = (rep.success && rep.numFailedTests === 0 && tr.status === 'passed') ? 'pass' : 'fail';
  return res;
}

async function gateTestGen(ctx, req) {
  if (process.env.FACTORY_GATE_ISOLATED !== '1') {
    return { ok: false, stage: 'gate_env', message: 'test_gen executes model output and runs only in an isolated gate (FACTORY_GATE_ISOLATED=1)' };
  }
  const { staticGates } = require('./lib/testcheck');
  const { applyMutant } = require('./lib/mutate');
  const card = req.unit;
  const profile = req.profile || {};
  const testPath = safeRel(card.provides.test_file).split(path.sep).join('/');
  const symbols = card.target.symbols;
  const out = { kind: 'test_gen', unit_id: card.unit_id, checks: {}, timings: {} };
  const t0 = Date.now();
  const st = staticGates({
    code: req.code, repoBase: ctx.base, testPath, targetFile: card.target.file, symbols,
    allowedPackages: (profile.jest || {}).allowedPackages || [],
  });
  Object.assign(out.checks, st.checks);
  out.timings.static_ms = Date.now() - t0;
  if (!st.result.ok) return Object.assign(out, { ok: false, stage: st.result.stage, message: st.result.message, detail: st.result.detail });

  const dir = makeRunDir(ctx, card.unit_id);
  try {
    const abs = path.join(dir, testPath);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, req.code);
    const j = await runJest(dir, profile, testPath);
    out.jest = j;
    out.timings.jest_ms = j.wallMs;
    const minTests = (card.tests && card.tests.min_tests) || 1;
    if (j.status === 'env') return Object.assign(out, { ok: false, stage: 'test_env', message: j.message });
    // jest itself did not run (no JSON report) or hung: an environment failure, never the model's (TEST_ENV)
    if (j.status === 'crash') return Object.assign(out, { ok: false, stage: 'test_env', message: `jest crashed before reporting: ${j.stderr}` });
    if (j.status === 'timeout') return Object.assign(out, { ok: false, stage: 'test_env', message: 'jest did not finish in time (open handles or import-time side effects?)' });
    if (j.status !== 'pass') {
      let msg;
      if (j.status === 'timeout') msg = 'jest did not finish in time';
      else if (j.status === 'crash') msg = `jest crashed: ${j.stderr}`;
      else if (j.suiteMessage) msg = `test suite failed to run: ${j.suiteMessage}`;
      else msg = j.failures.slice(0, 3).map((f) => `${f.title}: ${f.message}`).join('\n---\n');
      return Object.assign(out, { ok: false, stage: 'jest', message: msg.slice(0, 2400) });
    }
    if ((j.numPendingTests || 0) + (j.numTodoTests || 0) > 0) return Object.assign(out, { ok: false, stage: 'hygiene', message: `${j.numPendingTests} skipped/pending and ${j.numTodoTests} todo tests` });
    if (j.numTotalTests < minTests) return Object.assign(out, { ok: false, stage: 'min_tests', message: `jest ran ${j.numTotalTests} tests; this unit needs at least ${minTests}` });

    const mutants = (card.tests && card.tests.mutants) || [];
    const targetAbs = path.join(dir, safeRel(card.target.file));
    const original = fs.readFileSync(targetAbs, 'utf8');
    const results = [];
    const tm = Date.now();
    const par = Math.max(1, Number((profile.jest || {}).mutantParallel || 3));
    let next = 0;
    const worker = async () => {
      while (next < mutants.length) {
        const i = next++;
        const mu = mutants[i];
        let mdir = null;
        try {
          const text = applyMutant(original, mu);
          mdir = cloneRunDir(ctx, dir, card.unit_id); // same name pattern as the original run: a test cannot tell a mutant run by its path
          fs.writeFileSync(path.join(mdir, safeRel(card.target.file)), text);
          const r = await runJest(mdir, profile, testPath);
          results[i] = { id: mu.id, symbol: mu.symbol, op: mu.op, line: mu.line, killed: r.status !== 'pass', status: r.status };
        } catch (e) {
          results[i] = { id: mu.id, symbol: mu.symbol, op: mu.op, line: mu.line, killed: false, status: 'error', error: String(e.message) };
        } finally { if (mdir) rmrf(mdir); }
      }
    };
    await Promise.all(Array.from({ length: Math.min(par, mutants.length || 1) }, worker));
    out.timings.mutants_ms = Date.now() - tm;
    out.mutants = results;
    out.mutant_kill = { killed: results.filter((r) => r.killed).length, total: results.length };
    const bySym = {};
    for (const r of results) (bySym[r.symbol] = bySym[r.symbol] || []).push(r);
    const weak = [];
    for (const [sym, rs] of Object.entries(bySym)) {
      const k = rs.filter((r) => r.killed).length;
      const need = Math.ceil((2 / 3) * rs.length);
      if (k < need) weak.push(`${sym}: ${k}/${rs.length} seeded faults detected; undetected: ` + rs.filter((r) => !r.killed).map((s) => {
        const mu = mutants.find((x) => x.id === s.id);
        return `line ${mu.line} ${mu.op} \`${mu.original.slice(0, 70)}\` -> \`${mu.replacement.slice(0, 40)}\``;
      }).join('; '));
    }
    if (weak.length) return Object.assign(out, { ok: false, stage: 'mutants', message: weak.join('\n') });
    return Object.assign(out, { ok: true, stage: 'pass' });
  } finally {
    rmrf(dir);
    out.timings.total_ms = Date.now() - t0;
  }
}

// ---------------------------------------------------------------- main
async function handle(ctx, req) {
  if (!req || typeof req !== 'object') return { ok: false, stage: 'request', message: 'request must be a JSON object' };
  if (req.kind === 'version') return { ok: true, kind: 'version', versions: toolVersions(ctx) };
  if (!ctx.base || !fs.existsSync(ctx.base)) return { ok: false, stage: 'gate_env', message: `base repo dir missing: ${ctx.base}` };
  switch (req.kind) {
    case 'doc_map': return gateDocMap(ctx, req);
    case 'test_gen': return gateTestGen(ctx, req);
    case 'tsc_baseline': {
      const b = await tscBaseline(ctx, req.profile || {});
      return { ok: true, kind: 'tsc_baseline', count: b.count, ms: b.ms };
    }
    case 'tsc_check': {
      const d = await tscDelta(ctx, req.profile || {}, req.overlay || {}, 'check');
      if (d.error) return { ok: false, kind: 'tsc_check', stage: 'gate_env', message: d.error };
      return { ok: d.added.length === 0, kind: 'tsc_check', stage: d.added.length ? 'tsc' : 'pass', new: d.added, fixed: d.removed.length, baseline: d.baseline, after: d.after, ms: d.ms };
    }
    case 'assemble_docs': return assembleDocs(ctx, req);
    default: return { ok: false, stage: 'request', message: `unknown kind ${req.kind}` };
  }
}

async function main() {
  const a = parseArgs(process.argv.slice(2));
  fs.mkdirSync(a.scratch, { recursive: true });
  const ctx = { base: a.base, deps: a.deps, tools: a.tools, scratch: a.scratch };
  let res;
  try {
    const raw = a.request ? fs.readFileSync(a.request, 'utf8') : await readStdin();
    res = await handle(ctx, JSON.parse(raw));
  } catch (e) {
    res = { ok: false, stage: 'gate_error', message: String((e && e.stack) || e).slice(0, 2000) };
  }
  const text = JSON.stringify(res);
  process.stdout.write(a.sentinel ? `\n${SENTINEL}${text}\n` : text);
}

main();
