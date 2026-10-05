#!/usr/bin/env python3
"""Tests for ops/audit/etcd-secrets-scan.py against hand-built bbolt files.

Run: python3 ops/maintenance/tests/test_etcd_secrets_scan.py   (stdlib only)

The builder writes the same on-disk layout etcd's bbolt uses: two meta pages, a root
bucket leaf page holding the "key" bucket (non-inline, behind a branch page) and an
inline "meta" bucket, leaf pages of mvccpb.KeyValue records keyed by revision, and a
free page with leftover plaintext bytes (what a reencrypt leaves until a defragment).
"""
import hashlib
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("scan", os.path.join(HERE, "..", "lib", "etcd-secrets-scan.py"))
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

PS = 4096
PLAIN = scan.PLAINTEXT_SECRET_MAGIC


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def kv(key, value, mod_rev):
    msg = b"\x0a" + varint(len(key)) + key + b"\x10" + varint(mod_rev) + b"\x18" + varint(mod_rev) + b"\x20\x01"
    if value:
        msg += b"\x2a" + varint(len(value)) + value
    return msg


def revkey(main, sub=0, tomb=False):
    return struct.pack(">Q", main) + b"_" + struct.pack(">Q", sub) + (b"t" if tomb else b"")


def leaf_page(pgid, items, inline=False):
    """items: list of (flags, key, value)"""
    hdr = struct.pack("<QHHI", 0 if inline else pgid, 0x02, len(items), 0)
    elems = b""
    data = b""
    base = 16 * len(items)
    for n, (flags, k, v) in enumerate(items):
        pos = base - n * 16 + len(data)       # relative to this element's address
        elems += struct.pack("<IIII", flags, pos, len(k), len(v))
        data += k + v
    page = hdr + elems + data
    if inline:
        return page
    assert len(page) <= PS, "test page too large"
    return page.ljust(PS, b"\x00")


def branch_page(pgid, children):
    hdr = struct.pack("<QHHI", pgid, 0x01, len(children), 0)
    elems = b""
    data = b""
    base = 16 * len(children)
    for n, (k, child) in enumerate(children):
        pos = base - n * 16 + len(data)
        elems += struct.pack("<IIQ", pos, len(k), child)
        data += k
    return (hdr + elems + data).ljust(PS, b"\x00")


def meta_page(pgid, root, txid, npages):
    body = struct.pack("<IIII", scan.BBOLT_MAGIC, 2, PS, 0) + struct.pack("<QQ", root, 0) \
        + struct.pack("<QQQ", 2, npages, txid)
    h = 0xCBF29CE484222325
    for b in body:
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return (struct.pack("<QHHI", pgid, 0x04, 0, 0) + body + struct.pack("<Q", h)).ljust(PS, b"\x00")


def build(records_left, records_right, free_bytes=b""):
    """records_*: list of (revkey, kvbytes) for the two leaf pages under the branch."""
    pages = {}
    pages[2] = struct.pack("<QHHI", 2, 0x10, 0, 0).ljust(PS, b"\x00")          # freelist
    inline_meta = struct.pack("<QQ", 0, 0) + leaf_page(0, [(0, b"consistent_index", b"\x00" * 8)], inline=True)
    pages[3] = leaf_page(3, [(1, b"key", struct.pack("<QQ", 4, 0)), (1, b"meta", inline_meta)])
    pages[4] = branch_page(4, [(records_left[0][0], 5), (records_right[0][0], 6)])
    pages[5] = leaf_page(5, [(0, rk, v) for rk, v in records_left])
    pages[6] = leaf_page(6, [(0, rk, v) for rk, v in records_right])
    pages[7] = (struct.pack("<QHHI", 7, 0x10, 0, 0) + free_bytes).ljust(PS, b"\x00")  # stale free page
    npages = 8
    pages[0] = meta_page(0, 3, 1, npages)
    pages[1] = meta_page(1, 3, 2, npages)
    db = b"".join(pages[i] for i in range(npages))
    return db + hashlib.sha256(db).digest()


ENC = b"k8s:enc:aescbc:v1:aescbckey:" + os.urandom(48)
PLAIN_OBJ = PLAIN + b"\x12\x10\x0a\x05token" + b"\x00" * 8


class ScanTests(unittest.TestCase):
    def run_cli(self, data, *args):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            path = f.name
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = scan.main(list(args) + [path])
            return rc, out.getvalue()
        finally:
            os.unlink(path)

    def test_all_encrypted_with_stale_free_page(self):
        left = [(revkey(10), kv(b"/registry/secrets/default/a", ENC, 10)),
                (revkey(11), kv(b"/registry/configmaps/default/x", b"k8s\x00plain-cm", 11))]
        right = [(revkey(20), kv(b"/registry/secrets/kube-system/b", ENC, 20))]
        data = build(left, right, free_bytes=PLAIN_OBJ)
        r = scan.scan(data)
        self.assertEqual(r["live"]["aescbc"], 2)
        self.assertEqual(r["live"]["plaintext"], 0)
        self.assertEqual(r["raw_plaintext_secret_objects"], 1)
        rc, out = self.run_cli(data, "--expect", "encrypted")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli(data, "--expect", "encrypted", "--strict-raw")
        self.assertEqual(rc, 1, out)
        self.assertNotIn("token", out)

    def test_live_plaintext_fails_and_names_only_on_request(self):
        left = [(revkey(10), kv(b"/registry/secrets/default/a", PLAIN_OBJ, 10))]
        right = [(revkey(20), kv(b"/registry/secrets/default/b", ENC, 20))]
        data = build(left, right)
        rc, out = self.run_cli(data, "--expect", "encrypted")
        self.assertEqual(rc, 1)
        self.assertNotIn("default/a", out)
        rc, out = self.run_cli(data, "--expect", "encrypted", "--names")
        self.assertIn("default/a", out)
        rc, _ = self.run_cli(data, "--expect", "plaintext")
        self.assertEqual(rc, 1)

    def test_newest_revision_wins_and_history_is_counted(self):
        left = [(revkey(10), kv(b"/registry/secrets/default/a", PLAIN_OBJ, 10))]
        right = [(revkey(30), kv(b"/registry/secrets/default/a", ENC, 30))]
        r = scan.scan(build(left, right))
        self.assertEqual(r["live"]["aescbc"], 1)
        self.assertEqual(r["live"]["plaintext"], 0)
        self.assertEqual(r["history"]["plaintext"], 1)

    def test_tombstone_is_deleted_not_live(self):
        left = [(revkey(10), kv(b"/registry/secrets/default/gone", PLAIN_OBJ, 10))]
        right = [(revkey(40, tomb=True), kv(b"/registry/secrets/default/gone", b"", 40))]
        r = scan.scan(build(left, right))
        self.assertEqual(sum(r["live"].values()), 0)
        self.assertEqual(r["history"]["deleted"], 1)

    def test_canary_and_marker(self):
        marker = "aa-canary-0123456789abcdef"
        left = [(revkey(10), kv(b"/registry/secrets/default/aa-maint-encryption-canary", ENC, 10))]
        right = [(revkey(20), kv(b"/registry/secrets/default/b", ENC, 20))]
        rc, out = self.run_cli(build(left, right), "--expect", "encrypted",
                               "--canary", "default/aa-maint-encryption-canary", "--marker", marker)
        self.assertEqual(rc, 0, out)
        leak = PLAIN_OBJ + marker.encode()
        rc, out = self.run_cli(build(left, right, free_bytes=leak), "--expect", "encrypted", "--marker", marker)
        self.assertEqual(rc, 1, out)
        self.assertNotIn(marker, out)

    def test_plaintext_rollback_expectation(self):
        left = [(revkey(10), kv(b"/registry/secrets/default/a", PLAIN_OBJ, 10))]
        right = [(revkey(20), kv(b"/registry/secrets/default/b", PLAIN_OBJ, 20))]
        rc, _ = self.run_cli(build(left, right), "--expect", "plaintext")
        self.assertEqual(rc, 0)

    def test_not_bbolt(self):
        rc, _ = self.run_cli(b"\x00" * 9000, "--expect", "encrypted")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
