#!/usr/bin/env python3
"""Workstation pull/return over an authenticated operator tunnel.

Only batches explicitly approved by a team lead may be leased. Returned output
is independently gated in the factory before acceptance. No feeder is included.
"""
import argparse
import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time
from factory_queue.snapshot_queue import Queue, safe_path, encoded
import service
WORK = service.WORK

@contextlib.contextmanager
def lock():
    q = Queue(WORK / "queue")
    try:
        with q.lock():
            yield
    finally:
        q.db.close()

def pc_pull():
    from factory_engine.store import Store
    q = Queue(WORK / 'queue')
    try:
        with lock(), q.db:
            row = q.db.execute("SELECT * FROM batches WHERE state='ready' AND expires>? ORDER BY created LIMIT 1", (time.time()+900,)).fetchone()
            if not row:
                return {'empty': True}
            bid, spec = row['id'], json.loads(row['spec'])
            if not q.db.execute("UPDATE batches SET state='pc-claimed' WHERE id=? AND state='ready'", (bid,)).rowcount:
                return {'empty': True}
            q.event(bid, 'pc-claimed', 'authenticated operator tunnel; no automatic replay')
        folder = q.home / bid
        from factory_queue.snapshot_queue import verify_stored, ENGINE
        verify_stored(folder, spec)
        argv = [sys.executable, str(ENGINE), '--home', str(folder / 'engine'), 'submit', '--snapshot', str(folder / 'snapshot'), '--task', bid[:16], '--lanes', str(folder / 'lanes.json'), '--gate', 'local', '--no-start', '--json']
        for n, item in enumerate(spec['inputs']):
            argv += ['--' + item['type'], str(folder / f'input{n}.json')]
        subprocess.run(argv, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        store = Store(folder / 'engine/tasks' / bid[:16] / 'factory.db')
        try:
            return {'batch': bid, 'config': store.task()['config'], 'cards': [u['card'] for u in store.units()],
                    'files': {name: base64.b64encode(safe_path(folder / 'snapshot', name).read_bytes()).decode() for name in spec['snapshot']['files']}}
        finally:
            store.close()
    except Exception:
        if 'bid' in locals():
            q.state(bid, 'quarantine', 'workstation transfer preparation failed; inspect, no automatic replay')
        raise
    finally:
        q.db.close()


async def pc_return(data):
    from factory_engine.store import Store
    from factory_engine.engine import Engine
    if not isinstance(data.get("units"), list) or len(data["units"]) > 100:
        raise ValueError("bounded returned unit list required")
    ids = [unit.get("unit_id") for unit in data["units"]]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate returned unit")
    if any(len(str(unit.get("output", ""))) > 20000 for unit in data["units"]):
        raise ValueError("returned output exceeds unit byte cap")
    bid = data['batch']
    if not re.fullmatch('[0-9a-f]{64}', bid):
        raise ValueError('invalid batch')
    q = Queue(WORK / 'queue')
    try:
        row = q.db.execute('SELECT * FROM batches WHERE id=?', (bid,)).fetchone()
        if not row or row['state'] not in ('pc-claimed', 'done', 'quarantine'):
            raise ValueError('batch not leased to workstation')
        folder = q.home / bid
        from factory_queue.snapshot_queue import verify_stored
        verify_stored(folder, json.loads(row['spec']))
        store = Store(folder / 'engine/tasks' / bid[:16] / 'factory.db')
        try:
            engine = Engine(store.path.parent, store)
            await engine.runner.start({name: repo.root for name, repo in engine.repos.items()})
            await engine._baselines()
            for result in data['units']:
                unit = store.unit(result['unit_id'])
                if not unit or unit['kind'] != 'doc_map':
                    raise ValueError('result outside lease')
                if unit['status'] == 'accepted':
                    continue
                if result['status'] != 'accepted':
                    store.set_unit(unit['unit_id'], status='bounced', lane='workstation', bounce_class='workstation_RETURN', bounce={'reason': 'workstation did not accept unit'})
                    continue
                verdict = await engine.runner.run(unit['repo'], {'kind': 'doc_map', 'unit': unit['card'], 'code': result['output'], 'profile': engine.profiles[unit['repo']]}, 300)
                store.set_unit(unit['unit_id'], status='accepted' if verdict.get('ok') else 'bounced', lane='workstation', output=result['output'], gate=verdict)
            counts = store.counts()
            if counts['queued'] or counts['running']:
                raise ValueError('incomplete result set')
            q.state(bid, 'done' if counts['accepted'] == counts['total'] else 'quarantine', 'workstation results independently gated in factory')
            service.publish(q)
            return {'batch': bid, 'counts': counts, 'published': True}
        finally:
            if 'engine' in locals():
                await engine.runner.close()
            store.close()
    finally:
        q.db.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["pc-pull", "pc-return"])
    args = parser.parse_args(argv)
    if args.operation == "pc-pull":
        result = pc_pull()
    else:
        data = sys.stdin.buffer.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("returned result exceeds byte cap")
        result = asyncio.run(pc_return(json.loads(data)))
    print(json.dumps(result))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
