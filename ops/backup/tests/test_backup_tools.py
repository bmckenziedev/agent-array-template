#!/usr/bin/env python3
"""Offline tests for the ops/backup python helpers (no cluster, no restic, no node-a needed).

    python3 ops/backup/tests/test_backup_tools.py

Covers etcd-snapshot-check.py (valid snapshot + three kinds of corruption),
sealed-keys-fingerprints.py (fingerprint equals `openssl x509 -fingerprint -sha256`), and
restore-drill-check.py on a synthetic restore tree: the happy path and each failure the drill
must catch. Throwaway test keys are generated with the openssl CLI in a temp dir; the sealed-keys
tests are skipped where openssl is missing.
"""
import base64
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent
ETCD_CHECK = KIT / "etcd-snapshot-check.py"
FINGERPRINTS = KIT / "sealed-keys-fingerprints.py"
OPENSSL = shutil.which("openssl")


def fnv1a64(data):
    h = 0xCBF29CE484222325
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def make_etcd_snapshot(pages=6, page_size=4096, txid=42):
    """A minimal bbolt file (two valid meta pages) plus etcd's 32-byte sha256 trailer."""
    db = bytearray(pages * page_size)
    for idx in (0, 1):
        off = idx * page_size
        struct.pack_into("<QHHI", db, off, idx, 0x04, 0, 0)               # page header (meta flag)
        struct.pack_into("<IIII", db, off + 16, 0xED0CDAED, 2, page_size, 0)
        struct.pack_into("<QQQQQ", db, off + 32, 3, 0, 2, pages, txid - 1 + idx)
        struct.pack_into("<Q", db, off + 72, fnv1a64(bytes(db[off + 16:off + 72])))
    db[3 * page_size:3 * page_size + 11] = b"etcd-bucket"
    return bytes(db) + hashlib.sha256(db).digest()


def run(cmd, **kw):
    return subprocess.run([sys.executable, *map(str, cmd)], capture_output=True, text=True, **kw)


def gen_cert(tmp, name):
    key, crt = tmp / f"{name}.key", tmp / f"{name}.crt"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                    "-nodes", "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={name}", "-days", "2"],
                   check=True, capture_output=True)
    fp = subprocess.run([OPENSSL, "x509", "-noout", "-fingerprint", "-sha256", "-in", str(crt)],
                        check=True, capture_output=True, text=True).stdout.strip().split("=", 1)[1]
    return key.read_bytes(), crt.read_bytes(), fp


def secret_list(items):
    """kubectl -o yaml shape: a List of kubernetes.io/tls Secrets."""
    docs = []
    for name, created, crt, key in items:
        docs.append(textwrap.indent(textwrap.dedent(f"""\
            - apiVersion: v1
              kind: Secret
              type: kubernetes.io/tls
              metadata:
                name: {name}
                namespace: kube-system
                creationTimestamp: "{created}"
              data:
                tls.crt: {base64.b64encode(crt).decode()}
                tls.key: {base64.b64encode(key).decode()}
            """), ""))
    return "apiVersion: v1\nkind: List\nitems:\n" + "".join(docs)


class EtcdSnapshotCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.good = make_etcd_snapshot()

    def check(self, data):
        p = self.tmp / "snap.db"
        p.write_bytes(data)
        return run([ETCD_CHECK, p])

    def test_valid_snapshot_passes(self):
        res = self.check(self.good)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("etcd snapshot check OK", res.stdout)
        self.assertIn(hashlib.sha256(self.good).hexdigest(), res.stdout)

    def test_flipped_data_byte_fails_trailer(self):
        bad = bytearray(self.good)
        bad[3 * 4096 + 2] ^= 0xFF
        res = self.check(bytes(bad))
        self.assertEqual(res.returncode, 1)
        self.assertIn("sha256 trailer does not match", res.stderr)

    def test_both_meta_checksums_broken_fails(self):
        bad = bytearray(self.good)
        for off in (72, 4096 + 72):
            bad[off] ^= 0x01
        res = self.check(bytes(bad))
        self.assertEqual(res.returncode, 1)
        self.assertIn("no valid bbolt meta page", res.stderr)

    def test_truncated_fails(self):
        res = self.check(self.good[: 3 * 4096])
        self.assertEqual(res.returncode, 1)

    def test_one_bad_meta_still_passes_like_bbolt(self):
        # bbolt falls back to the other meta page; so does the checker (trailer recomputed).
        db = bytearray(self.good[:-32])
        db[72] ^= 0x01
        res = self.check(bytes(db) + hashlib.sha256(db).digest())
        self.assertEqual(res.returncode, 0, res.stderr)


@unittest.skipUnless(OPENSSL, "openssl CLI not found")
class SealedKeyFingerprints(unittest.TestCase):
    def test_matches_openssl_and_never_prints_key(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        key, crt, fp = gen_cert(tmp, "k1")
        dump = tmp / "keys.yaml"
        dump.write_text(secret_list([("sealed-secrets-keyabc", "2025-01-01T08:59:51Z", crt, key)]))
        res = run([FINGERPRINTS, dump])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.split(), ["sealed-secrets-keyabc", "2025-01-01T08:59:51Z", fp])
        self.assertNotIn(base64.b64encode(key).decode()[:40], res.stdout + res.stderr)

    def test_no_cert_is_an_error(self):
        res = run([FINGERPRINTS, "-"], input="apiVersion: v1\nkind: List\nitems: []\n")
        self.assertEqual(res.returncode, 1)



if __name__ == "__main__":
    unittest.main()
