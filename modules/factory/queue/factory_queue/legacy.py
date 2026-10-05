"""Bounded, attended approved-work queue. No dependencies outside stdlib."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / 'engine/factory.py'
TERMINAL = ('done', 'quarantine', 'cancelled', 'deleted')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def safe_path(root, name):
    p = PurePosixPath(name)
    if not p.parts or p.is_absolute() or any(x in ('..', '.', '.git') or ':' in x or '\\' in x for x in p.parts):
        raise ValueError('unsafe relative path')
    # Refuse credential-like files before opening them, including in manifests.
    if any(x.lower() in ('.ssh', '.kube', '.codex', '.claude', '.env') or
           any(s in x.lower() for s in ('credential', 'auth.json', 'login', 'id_rsa', 'id_ed25519')) for x in p.parts):
        raise ValueError('credential path forbidden')
    dest = root.joinpath(*p.parts)
    for parent in [dest, *dest.parents]:
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('links forbidden')
        if parent == root:
            break
    return dest


def external(path):
    p = Path(path).resolve()
    if p == REPO or REPO in p.parents:
        raise ValueError('state and snapshots must be outside source checkout')
    for parent in [p, *p.parents]:
        if (parent / '.git').exists():
            raise ValueError('path is inside a source checkout')
    return p


def verify_stored(folder, spec):
    root = folder / 'snapshot'
    expected = spec['snapshot']['files']
    found = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('stored snapshot link forbidden')
        for name in files:
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if relative not in expected:
                raise ValueError('unapproved snapshot file')
            found.add(relative)
    if found != set(expected):
        raise ValueError('snapshot file set changed')
    for name, sha in expected.items():
        if hashlib.sha256(safe_path(root, name).read_bytes()).hexdigest() != sha:
            raise ValueError('changed snapshot digest')


def validate(batch):
    if len(encoded(batch)) > 1024 * 1024:
        raise ValueError('batch JSON exceeds 1 MiB')
    if set(batch) != {'version', 'snapshot', 'inputs', 'lanes', 'limits'} or batch['version'] != 1:
        raise ValueError('invalid batch fields/version')
    s, lim = batch['snapshot'], batch['limits']
    if set(s) != {'root', 'files', 'digest'} or not isinstance(s['files'], dict) or not 1 <= len(s['files']) <= 10000:
        raise ValueError('explicit snapshot file manifest required')
    external(s['root'])
    if digest(s['files']) != s['digest']:
        raise ValueError('snapshot manifest digest mismatch')
    if set(lim) != {'units', 'seconds', 'retention_seconds'}:
        raise ValueError('limits required')
    for k, maximum in [('units', 100), ('seconds', 900), ('retention_seconds', 43200)]:
        if type(lim[k]) is not int or not 1 <= lim[k] <= maximum:
            raise ValueError('limit out of range')
    count = 0
    if not isinstance(batch['inputs'], list) or not 1 <= len(batch['inputs']) <= 10:
        raise ValueError('bounded inputs required')
    for item in batch['inputs']:
        if set(item) != {'type', 'value'} or item['type'] not in ('template', 'cards'):
            raise ValueError('only template/cards inputs allowed')
        values = item['value'] if item['type'] == 'cards' else [item['value']]
        if not isinstance(values, list) or not values:
            raise ValueError('empty cards')
        for v in values:
            if v.get('kind') != 'doc_map':
                raise ValueError('only doc_map allowed')
            if item['type'] == 'template':
                expand = v['expand']
                if not expand.get('filter', {}).get('symbols') or not isinstance(v.get('profile'), dict):
                    raise ValueError('template must pin symbols and inline profile')
                cap = expand.get('cap')
                if type(cap) is not int or not 1 <= cap <= lim['units']:
                    raise ValueError('template cap required')
                count += cap
            else:
                count += 1
        # No host paths or keys can be smuggled through profile/exemplar/retry.
        def inspect(v):
            if isinstance(v, dict):
                for key, val in v.items():
                    if key in ('key_file', 'key_env', 'api_key', 'baseJestConfig') or (key == 'file' and v is not item['value'] and 'text' in v):
                        raise ValueError('host/credential input forbidden')
                    if key == 'kimi' and val is not False:
                        raise ValueError('paid retry forbidden')
                    if key == 'max_generations' and (type(val) is not int or not 1 <= val <= 3):
                        raise ValueError('retry cap exceeded')
                    if key == 'exemplar' and isinstance(val, dict) and 'file' in val:
                        raise ValueError('inline exemplar required')
                    if key in ('sources', 'roots', 'include', 'exclude') and isinstance(val, list):
                        for name in val:
                            safe_path(Path('/unopened-boundary'), name)
                    if key == 'file' and isinstance(val, str):
                        safe_path(Path('/unopened-boundary'), val)
                    inspect(val)
            elif isinstance(v, list):
                for val in v:
                    inspect(val)
        inspect(item['value'])
    if count > lim['units']:
        raise ValueError('batch unit cap exceeded')
    lanes = batch['lanes']
    if set(lanes) - {'lanes', 'gate_parallel', 'max_inflight_units', 'infra_retries'}:
        raise ValueError('routing overrides forbidden')
    if set(lanes['lanes']) != {'lane-gpu-a', 'lane-gpu-b'}:
        raise ValueError('both existing local lanes required')
    for lane in lanes['lanes'].values():
        if set(lane) - {'gpu', 'tier', 'concurrency', 'ctx', 'prompt_cap', 'endpoint'}:
            raise ValueError('unknown lane fields')
        endpoint = lane['endpoint']
        if set(endpoint) - {'base_url', 'api', 'model', 'template', 'timeout_s'}:
            raise ValueError('credential/unknown endpoint settings forbidden')
        url = urlsplit(endpoint['base_url'])
        host = url.hostname or ''
        if url.scheme != 'http' or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('plain local model endpoint required')
        if host not in ('127.0.0.1', 'localhost', 'lane-gpu-a', 'lane-gpu-b') and not host.endswith('.' + os.environ.get('FACTORY_MODELS_NAMESPACE', 'agent-array-models') + '.svc.cluster.local'):
            raise ValueError('endpoint not in local allowlist')
        if endpoint['api'] not in ('llamacpp', 'openai-completions') or any(x in endpoint['model'].lower() for x in ('kimi', 'claude', 'codex', 'gpt', 'moonshot')):
            raise ValueError('paid/frontier model forbidden')
        if not 1 <= lane.get('concurrency', 1) <= 4:
            raise ValueError('lane concurrency exceeded')
        if type(endpoint.get('timeout_s', 300)) not in (int, float) or not 1 <= endpoint.get('timeout_s', 300) <= 300:
            raise ValueError('endpoint timeout exceeded')
    if not 1 <= lanes.get('gate_parallel', 1) <= 3 or not 1 <= lanes.get('max_inflight_units', 1) <= 10 or not 0 <= lanes.get('infra_retries', 0) <= 3:
        raise ValueError('engine backpressure/retry limits exceeded')


class Queue:
    def __init__(self, home):
        self.home = external(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.home / 'queue.sqlite', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, spec TEXT NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, reason TEXT);
          CREATE TABLE IF NOT EXISTS ledger(seq INTEGER PRIMARY KEY, at REAL, id TEXT,
            event TEXT, detail TEXT);''')

    def event(self, bid, event, detail=None):
        self.db.execute('INSERT INTO ledger(at,id,event,detail) VALUES(?,?,?,?)',
                        (time.time(), bid, event, json.dumps(detail)))

    def rows(self):
        return [dict(r) for r in self.db.execute('SELECT id,state,created,expires,reason FROM batches ORDER BY created')]

    def enqueue(self, spec, dry=False, *, actor=None, team_id=None):
        validate(spec)
        if not dry and (not actor or not team_id):
            raise PermissionError('lead approval actor and team required')
        identity = json.loads(encoded(spec))
        del identity['snapshot']['root']
        bid = digest(identity)
        # Verify every explicitly named file; never enumerate a source/estate.
        total = 0
        root = external(spec['snapshot']['root'])
        for name, sha in spec['snapshot']['files'].items():
            p = safe_path(root, name)
            total += p.stat().st_size
            if total > 64 * 1024 * 1024 or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise ValueError('snapshot bytes changed or size cap exceeded')
        if dry:
            return {'id': bid, 'dry_run': True, 'units_cap': spec['limits']['units']}
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM batches WHERE id=?', (bid,)).fetchone():
                self.db.rollback()
                return {'id': bid, 'deduplicated': True}
            if self.db.execute("SELECT count(*) FROM batches WHERE state != 'deleted'").fetchone()[0] >= 32:
                raise ValueError('backpressure: 32 retained batches; reap first')
            target = self.home / bid
            if target.exists():
                # Incomplete ingestion after crash. This path is hash-derived, call-owned.
                shutil.rmtree(target)
            (target / 'snapshot').mkdir(parents=True)
            for name, sha in spec['snapshot']['files'].items():
                src, dst = safe_path(root, name), safe_path(target / 'snapshot', name)
                data = src.read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError('snapshot changed during copy')
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
            for n, item in enumerate(spec['inputs']):
                (target / f'input{n}.json').write_bytes(encoded(item['value']))
            (target / 'lanes.json').write_bytes(encoded(spec['lanes']))
            now = time.time()
            self.db.execute('INSERT INTO batches VALUES(?,?,?,?,?,NULL)',
                            (bid, encoded(spec).decode(), 'ready', now, now + spec['limits']['retention_seconds']))
            self.event(bid, 'approved', {'digest': spec['snapshot']['digest'], 'actor': actor, 'actor_kind': 'lead', 'team_id': team_id})
            self.db.commit()
        except BaseException:
            self.db.rollback()
            if 'target' in locals() and target.exists():
                shutil.rmtree(target)
            raise
        return {'id': bid, 'state': 'ready'}

    @contextlib.contextmanager
    def lock(self):
        # OS lock released on crash; never stale-PID based takeover.
        with (self.home / 'consumer.lock').open('a+b') as f:
            f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                if os.name == 'nt':
                    f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def state(self, bid, state, reason=None):
        with self.db:
            self.db.execute('UPDATE batches SET state=?,reason=? WHERE id=?', (state, reason, bid))
            self.event(bid, state, reason)

    def run(self, argv, seconds, cancelled, cwd):
        # Never shell=True. Output is discarded: no secret/prompt logging.
        env = {k: v for k, v in os.environ.items() if not k.startswith(('FACTORY_', 'OPENAI_', 'ANTHROPIC_', 'LITELLM_', 'MOONSHOT_'))}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['FACTORY_HOME'] = str(cwd / 'engine')
        if os.name != 'nt':
            p = subprocess.Popen([sys.executable, str(Path(__file__).parents[1] / "supervisor.py"), *argv],
                                 cwd=cwd, env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
                        # No unsafe fallback to killing only the parent on Windows.
            raise ValueError('consumer requires POSIX process groups; Windows supports preparation/list only')
        deadline = time.monotonic() + seconds
        try:
            while p.poll() is None:
                if cancelled() or time.monotonic() >= deadline:
                    raise TimeoutError('cancelled or timed out')
                time.sleep(.05)
            return p.returncode
        finally:
            p.stdin.close()
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.terminate()
            p.wait()

    def consume(self, max_batches=1, seconds=900, runner=None):
        if not 1 <= max_batches <= 32 or not 1 <= seconds <= 900:
            raise ValueError('consumer caps exceeded')
        runner = runner or self.run
        stop = [False]
        old = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            old[sig] = signal.signal(sig, lambda *_: stop.__setitem__(0, True))
        done = []
        try:
            with self.lock():
                # Ambiguous submissions are never replayed, even if engine has no task DB.
                for r in self.db.execute("SELECT id FROM batches WHERE state='claimed'").fetchall():
                    self.state(r['id'], 'quarantine', 'interrupted dispatch; inspect engine state, no automatic retry')
                deadline = time.monotonic() + seconds
                for _ in range(max_batches):
                    if stop[0] or time.monotonic() >= deadline:
                        break
                    row = self.db.execute("SELECT * FROM batches WHERE state='ready' ORDER BY created LIMIT 1").fetchone()
                    if not row:
                        break
                    bid, spec = row['id'], json.loads(row['spec'])
                    folder = self.home / bid
                    with self.db:
                        claimed = self.db.execute("UPDATE batches SET state='claimed' WHERE id=? AND state='ready'", (bid,)).rowcount
                        if claimed:
                            self.event(bid, 'claimed')
                    if not claimed:
                        continue
                    # The claim is fsynced before any process can start.
                    try:
                        if time.time() >= row['expires']:
                            raise ValueError('retention expired')
                        validate(spec)
                        verify_stored(folder, spec)
                        for n, item in enumerate(spec['inputs']):
                            if (folder / f'input{n}.json').read_bytes() != encoded(item['value']):
                                raise ValueError('changed input digest')
                        if (folder / 'lanes.json').read_bytes() != encoded(spec['lanes']):
                            raise ValueError('changed lanes digest')
                        argv = [sys.executable, str(ENGINE), '--home', str(folder / 'engine'), 'submit',
                                '--snapshot', str(folder / 'snapshot'), '--task', bid[:16], '--lanes', str(folder / 'lanes.json'),
                                '--gate', 'local', '--foreground', '--max-runtime', str(spec['limits']['seconds']), '--json']
                        for n, item in enumerate(spec['inputs']):
                            argv += ['--' + item['type'], str(folder / f'input{n}.json')]
                        rc = runner(argv, min(spec['limits']['seconds'], max(.01, deadline-time.monotonic())),
                                    lambda: stop[0] or self.db.execute('SELECT state FROM batches WHERE id=?', (bid,)).fetchone()[0] == 'cancelled', folder)
                        if rc != 0:
                            raise ValueError('engine failure; inspect task')
                        # Engine submit exit 0 does not mean drained. Inspect its durable unit states.
                        dbpath = folder / 'engine/tasks' / bid[:16] / 'factory.db'
                        with sqlite3.connect(f'file:{dbpath.as_posix()}?mode=ro', uri=True) as engine_db:
                            counts = dict(engine_db.execute('SELECT status,count(*) FROM unit GROUP BY status'))
                        if not counts or any(k != 'accepted' for k in counts) or sum(counts.values()) > spec['limits']['units']:
                            raise ValueError('bounce, pause, empty or unit cap: manual review required')
                        self.state(bid, 'done')
                    except Exception as exc:
                        self.state(bid, 'quarantine', str(exc) if isinstance(exc, (ValueError, TimeoutError)) else 'runtime failure')
                    done.append(bid)
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
        return {'processed': done, 'empty': not self.db.execute("SELECT 1 FROM batches WHERE state='ready'").fetchone()}

    def export(self, bid, destination):
        if len(bid) != 64 or any(c not in '0123456789abcdef' for c in bid):
            raise ValueError('invalid batch id')
        dest = external(destination)
        if self.home == dest or self.home in dest.parents:
            raise ValueError('export must be outside queue state')
        with self.lock():
            row = self.db.execute('SELECT * FROM batches WHERE id=?', (bid,)).fetchone()
            if not row or row['state'] != 'done' or row['expires'] <= time.time():
                raise ValueError('only completed unexpired batches can export')
            folder = self.home / bid
            verify_stored(folder, json.loads(row['spec']))
            rc = self.run([sys.executable, str(ENGINE), '--home', str(folder / 'engine'),
                           'finish', bid[:16], '--gate', 'local', '--json'], 900, lambda: False, folder)
            if rc != 0:
                self.state(bid, 'quarantine', 'integration/export failure')
                raise ValueError('finish requires manual review')
            bundle = folder / 'engine/tasks' / bid[:16] / 'bundle.tgz'
            data = bundle.read_bytes()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            receipt = {'artifact_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                       'destination': str(dest), 'applied': False}
            with self.db:
                self.event(bid, 'export', receipt)
            return receipt

    def reap(self):
        receipts = []
        with self.lock():
            for row in self.db.execute("SELECT * FROM batches WHERE expires<=? AND state!='deleted'", (time.time(),)).fetchall():
                bid = row['id']
                path = self.home / bid
                if path.exists():
                    shutil.rmtree(path)
                receipt = {'snapshot_digest': json.loads(row['spec'])['snapshot']['digest'], 'method': 'filesystem unlink; not secure erase', 'absent': not path.exists()}
                with self.db:
                    self.db.execute("UPDATE batches SET state='deleted',spec='{}' WHERE id=?", (bid,))
                    self.event(bid, 'deletion_receipt', receipt)
                receipts.append(bid)
        return receipts

    def cancel(self, bid):
        with self.db:
            changed = self.db.execute("UPDATE batches SET state='cancelled',reason='team lead cancellation' WHERE id=? AND state IN ('ready','claimed')", (bid,)).rowcount
            if not changed:
                raise ValueError('batch absent or no longer cancellable')
            self.event(bid, 'cancelled', 'team lead cancellation')
        return {'cancelled': bid}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    e = sub.add_parser('enqueue'); e.add_argument('batch'); e.add_argument('--dry-run', action='store_true')
    e.add_argument('--actor'); e.add_argument('--team')
    sub.add_parser('list'); sub.add_parser('status'); sub.add_parser('reap'); sub.add_parser('ledger')
    c = sub.add_parser('consume'); c.add_argument('--max-batches', type=int, default=1); c.add_argument('--seconds', type=int, default=900)
    c = sub.add_parser('cancel'); c.add_argument('id')
    x = sub.add_parser('export'); x.add_argument('id'); x.add_argument('destination')
    args = p.parse_args()
    q = Queue(args.home)
    try:
        if args.command == 'enqueue':
            result = q.enqueue(json.loads(Path(args.batch).read_text(encoding='utf-8')), args.dry_run, actor=args.actor, team_id=args.team)
        elif args.command in ('list', 'status'):
            result = {'batches': q.rows(), 'empty': not q.rows()}
        elif args.command == 'consume':
            result = q.consume(args.max_batches, args.seconds)
        elif args.command == 'reap':
            result = {'deleted': q.reap()}
        elif args.command == 'export':
            result = q.export(args.id, args.destination)
        elif args.command == 'cancel':
            result = q.cancel(args.id)
        else:
            result = [dict(r) for r in q.db.execute('SELECT * FROM ledger ORDER BY seq')]
        print(json.dumps(result))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)})); return 2
    finally:
        q.db.close()


if __name__ == '__main__':
    sys.exit(main())
