"""Ported durable lease and safe snapshot transfer mechanisms, independent of the engine."""
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from lane import transport


def safe_path(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    # Reject alternate Windows separators, drive names and filesystem traversal.
    if path.is_absolute() or not path.parts or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('unsafe snapshot path')
    result = root.joinpath(*path.parts).resolve()
    if root.resolve() not in result.parents:
        raise ValueError('snapshot path leaves root')
    return result


def remote(config, schedule, operation, payload=None):
    if operation not in {'device-pull', 'device-return', 'device-heartbeat'}:
        raise ValueError('unsupported transfer operation')
    command = transport(config, schedule,
                        ['factory-worker-transport', operation, '--device', config['device_id']])
    command.insert(command.index('exec') + 1, '-i')
    result = subprocess.run(command, input=json.dumps(payload or {}).encode(),
                            capture_output=True, timeout=900,
                            env=dict(os.environ, KUBECTL_REMOTE_COMMAND_WEBSOCKETS='false'))
    if result.returncode:
        raise RuntimeError('factory transfer failed')
    return json.loads(result.stdout)


def resume(root: Path):
    for marker in sorted(root.glob('*/lease.json')):
        lease = json.loads(marker.read_text())
        if not lease.get('returned'):
            return marker.parent
    return None


def stage(root: Path, data: dict) -> Path:
    batch = data['batch']
    if not isinstance(batch, str) or not re.fullmatch('[0-9a-f]{64}', batch):
        raise ValueError('invalid batch identifier')
    if any(card.get('kind') != 'doc_map' for card in data['cards']):
        raise ValueError('unsupported task kind')
    task = root / batch
    snapshot = task / 'snapshot'
    snapshot.mkdir(parents=True, exist_ok=True)
    total = 0
    # Bound disk use before writing a transport-provided snapshot.
    for name, value in data['files'].items():
        content = base64.b64decode(value, validate=True)
        total += len(content)
        if total > 256 * 1024 * 1024:
            raise ValueError('snapshot exceeds device limit')
        destination = safe_path(snapshot, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (task / 'work.json').write_text(json.dumps(data['cards']), encoding='utf-8')
    (task / 'lease.json').write_text(json.dumps({'batch': batch, 'returned': False}), encoding='utf-8')
    return task


def publish(task: Path, send, units: list[dict]) -> bool:
    if any(unit.get('status') in ('queued', 'running') for unit in units):
        return False
    marker = task / 'lease.json'
    lease = json.loads(marker.read_text())
    result = send({'batch': lease['batch'], 'units': units})
    if not result.get('published'):
        return False
    lease['returned'] = True
    temp = marker.with_suffix('.tmp')
    temp.write_text(json.dumps(lease), encoding='utf-8')
    temp.replace(marker)
    return True
