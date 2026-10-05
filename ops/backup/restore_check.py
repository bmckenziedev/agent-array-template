"""Offline restored-artifact checks; no production restore or live requests."""
import importlib.util
import base64
import json
from contextlib import closing
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys

import yaml


def validate_restore(root: Path, login_root: str, require_encryption: bool = True) -> dict[str, int]:
    login = root.joinpath(*PurePosixPath(login_root).parts[1:])
    if login.exists():
        raise ValueError('login material present in restored tree')
    keys = list(root.rglob('sealed-controller-key.yaml'))
    dbs = list(root.rglob('grafana.db'))
    snapshots = [p for p in root.rglob('*') if p.is_file() and 'snapshots' in p.parts]
    if not keys or not dbs or not snapshots:
        raise ValueError('required restored artifacts missing')
    tokens = list(root.rglob('cluster-server-token'))
    configs = list(root.rglob('encryption-provider-config.json'))
    if not tokens or any(not token.read_bytes().strip() for token in tokens):
        raise ValueError('cluster bootstrap token missing')
    if require_encryption and not configs:
        raise ValueError('encryption provider configuration missing')
    for config in configs:
        data = json.loads(config.read_text())
        if data.get('kind') != 'EncryptionConfiguration' or not data.get('resources'):
            raise ValueError('encryption provider configuration invalid')
    certs = 0
    for key in keys:
        documents = list(yaml.safe_load_all(key.read_text(encoding='utf-8')))
        secrets = [item for doc in documents for item in (doc.get('items', []) if doc.get('kind') == 'List' else [doc])]
        if not secrets or any(not {'tls.key', 'tls.crt'} <= set(item.get('data', {})) for item in secrets):
            raise ValueError('controller key inventory incomplete')
        for item in secrets:
            private = base64.b64decode(item['data']['tls.key'], validate=True)
            public = base64.b64decode(item['data']['tls.crt'], validate=True)
            cert_key = subprocess.run(['openssl', 'x509', '-pubkey', '-noout'], input=public,
                                      capture_output=True, check=True).stdout
            private_key = subprocess.run(['openssl', 'pkey', '-pubout'], input=private,
                                         capture_output=True, check=True).stdout
            if cert_key != private_key:
                raise ValueError('sealing key does not match its certificate')
        certs += len(secrets)
        # Only certificate fingerprints are printed by this ported helper.
        subprocess.run([sys.executable, str(Path(__file__).with_name('sealed-keys-fingerprints.py')),
                        str(key)], check=True, stdout=subprocess.DEVNULL)
    for snapshot in snapshots:
        subprocess.run([sys.executable, str(Path(__file__).with_name('etcd-snapshot-check.py')),
                        '--quiet', str(snapshot)], check=True, stdout=subprocess.DEVNULL)
    for path in dbs:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Grafana integrity failed')
    return {'keys': certs, 'databases': len(dbs), 'snapshots': len(snapshots)}
