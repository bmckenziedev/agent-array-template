import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from restore_check import validate_restore
from tests.test_backup_tools import gen_cert, make_etcd_snapshot, secret_list, OPENSSL


@unittest.skipUnless(OPENSSL, 'OpenSSL unavailable')
class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        key, cert, _ = gen_cert(self.root, 'synthetic-sealing-key')
        self.keys = self.root / 'sealed-controller-key.yaml'
        self.keys.write_text(secret_list([('synthetic-key', '2025-01-01T00:00:00Z', cert, key)]))
        snapshots = self.root / 'snapshots'
        snapshots.mkdir()
        self.snapshot = snapshots / 'snapshot.db'
        self.snapshot.write_bytes(make_etcd_snapshot())
        (self.root / 'cluster-server-token').write_text('synthetic-bootstrap-material')
        (self.root / 'encryption-provider-config.json').write_text(json.dumps({
            'kind': 'EncryptionConfiguration',
            'resources': [{'resources': ['secrets'], 'providers': [{'identity': {}}]}]}))
        with closing(sqlite3.connect(self.root / 'grafana.db')) as db:
            db.execute('CREATE TABLE dashboard (id INTEGER)')
            db.commit()

    def test_restored_artifacts_are_validated_offline(self):
        self.assertEqual(validate_restore(self.root, '/var/lib/logins'),
                         {'keys': 1, 'databases': 1, 'snapshots': 1})

    def test_login_material_is_rejected(self):
        (self.root / 'var/lib/logins').mkdir(parents=True)
        with self.assertRaises(ValueError):
            validate_restore(self.root, '/var/lib/logins')

    def test_missing_keys_rejected(self):
        self.keys.unlink()
        with self.assertRaises(ValueError):
            validate_restore(self.root, '/var/lib/logins')

    def test_missing_bootstrap_material_rejected(self):
        (self.root / 'cluster-server-token').unlink()
        with self.assertRaises(ValueError):
            validate_restore(self.root, '/var/lib/logins')

    def test_mismatched_controller_key_rejected(self):
        key, _, _ = gen_cert(self.root, 'different-synthetic-key')
        _, cert, _ = gen_cert(self.root, 'original-synthetic-key')
        self.keys.write_text(secret_list([('key', '2025-01-01T00:00:00Z', cert, key)]))
        with self.assertRaises(ValueError):
            validate_restore(self.root, '/var/lib/logins')

    def test_corrupt_snapshot_rejected(self):
        self.snapshot.write_bytes(b'corrupt')
        with self.assertRaises(Exception):
            validate_restore(self.root, '/var/lib/logins')
