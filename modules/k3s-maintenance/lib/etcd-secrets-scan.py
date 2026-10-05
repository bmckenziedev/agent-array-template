#!/usr/bin/env python3
"""etcd-secrets-scan.py -- are the Kubernetes Secrets inside an etcd snapshot encrypted?

Usage:
  etcd-secrets-scan.py [--expect encrypted|plaintext] [--strict-raw] [--marker TEXT]
                       [--canary NS/NAME] [--names] SNAPSHOT_FILE

Reads a snapshot written by `k3s etcd-snapshot save` (a bbolt database plus a 32-byte sha256
trailer) READ-ONLY, with the standard library only (no external etcd tooling is required).
It never prints a value: only counts, encryption provider names and, with --names, the
namespace/name of Secrets that are still plaintext (names are not secret material).

What it looks at:
  live      walks the bbolt B+tree of etcd's "key" bucket (the MVCC keyspace that etcd
            would serve) and, for every /registry/secrets/<ns>/<name> key, classifies the
            NEWEST revision: aescbc / secretbox / other encryption / plaintext / deleted.
  history   every revision still in the keyspace (not yet compacted), same classes.
  raw       byte search over the WHOLE file, free pages included: plaintext Secret objects
            (the protobuf magic k8s\\0 + TypeMeta v1/Secret) and, with --marker, a canary
            string. After a reencrypt, an apiserver compaction (every 5 min) and a k3s
            restart (k3s defragments etcd at start-up) both should be 0.

Exit status:
  0  the expectation holds (or no --expect given)
  1  the expectation failed
  2  usage error, unreadable file, or not a bbolt file
  --expect encrypted : every live Secret is encrypted, the canary (if given) is encrypted,
                       and the marker (if given) never appears in raw bytes. With
                       --strict-raw, no plaintext Secret object may appear in raw bytes
                       either (free pages included).
  --expect plaintext : no live Secret is encrypted (used to verify a rollback).
"""
import argparse
import struct
import sys
from collections import Counter

BBOLT_MAGIC = 0xED0CDAED
PAGE_HEADER = 16
BRANCH_PAGE = 0x01
LEAF_PAGE = 0x02
META_PAGE = 0x04
BUCKET_LEAF_FLAG = 0x01
SECRETS_PREFIX = b"/registry/secrets/"
# protobuf-encoded core/v1 Secret as the apiserver stores it unencrypted:
# "k8s\0" + Unknown{typeMeta{apiVersion:"v1", kind:"Secret"}}
PLAINTEXT_SECRET_MAGIC = b"k8s\x00\x0a\x0c\x0a\x02v1\x12\x06Secret"
MAX_PAGES_VISITED = 5_000_000


class ScanError(Exception):
    pass


def varint(buf, i):
    shift = 0
    result = 0
    while True:
        if i >= len(buf):
            raise ScanError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ScanError("varint too long")


def parse_keyvalue(buf):
    """Minimal mvccpb.KeyValue decoder: 1 key, 2 create_revision, 3 mod_revision,
    4 version, 5 value, 6 lease. Returns (key, value)."""
    i = 0
    key = b""
    value = b""
    while i < len(buf):
        tag, i = varint(buf, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            _, i = varint(buf, i)
        elif wire == 2:
            n, i = varint(buf, i)
            data = buf[i:i + n]
            i += n
            if field == 1:
                key = data
            elif field == 5:
                value = data
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        else:
            raise ScanError("unexpected protobuf wire type %d" % wire)
    return key, value


def classify(value):
    if not value:
        return "deleted"
    if value.startswith(b"k8s:enc:aescbc:v1:"):
        return "aescbc"
    if value.startswith(b"k8s:enc:secretbox:v1:"):
        return "secretbox"
    if value.startswith(b"k8s:enc:"):
        return "encrypted-other"
    if value.startswith(b"k8s\x00") or value.startswith(b"{"):
        return "plaintext"
    return "unknown"


ENCRYPTED = ("aescbc", "secretbox", "encrypted-other")


class Bolt:
    def __init__(self, data):
        self.data = data
        self.page_size = None
        self.root = None
        self._read_meta()

    def _read_meta(self):
        metas = []
        # meta page 0 is at offset 0; meta page 1 is at offset page_size, which we only
        # learn from a valid meta page 0 (or guess the common sizes if page 0 is damaged).
        candidates = [0]
        if len(self.data) >= PAGE_HEADER + 16:
            ps = struct.unpack_from("<I", self.data, PAGE_HEADER + 8)[0]
            if 512 <= ps <= 65536:
                candidates.append(ps)
        candidates += [4096, 8192, 16384, 65536]
        seen = set()
        for off in candidates:
            if off in seen or off + PAGE_HEADER + 64 > len(self.data):
                continue
            seen.add(off)
            _, flags, _, _ = struct.unpack_from("<QHHI", self.data, off)
            m = off + PAGE_HEADER
            magic, version, psize = struct.unpack_from("<III", self.data, m)
            if not flags & META_PAGE or magic != BBOLT_MAGIC or version != 2:
                continue
            root = struct.unpack_from("<Q", self.data, m + 16)[0]
            txid = struct.unpack_from("<Q", self.data, m + 48)[0]
            metas.append((txid, psize, root))
        if not metas:
            raise ScanError("no valid bbolt meta page (not an etcd snapshot?)")
        txid, self.page_size, self.root = max(metas)

    def _page(self, pgid):
        off = pgid * self.page_size
        if off + PAGE_HEADER > len(self.data):
            raise ScanError("page %d beyond end of file" % pgid)
        return off

    def _elements(self, buf, off):
        _, flags, count, _ = struct.unpack_from("<QHHI", buf, off)
        base = off + PAGE_HEADER
        if flags & BRANCH_PAGE:
            for n in range(count):
                e = base + n * 16
                _, _, child = struct.unpack_from("<IIQ", buf, e)
                yield ("branch", child, None, None)
        elif flags & LEAF_PAGE:
            for n in range(count):
                e = base + n * 16
                eflags, pos, ksize, vsize = struct.unpack_from("<IIII", buf, e)
                k = e + pos
                yield ("leaf", eflags, buf[k:k + ksize], buf[k + ksize:k + ksize + vsize])
        else:
            raise ScanError("page at offset %d is neither branch nor leaf (flags 0x%x)" % (off, flags))

    def walk(self, root_pgid, inline=None):
        """Yield (flags, key, value) for every leaf element of a bucket."""
        if root_pgid == 0:
            if inline is None:
                return
            for kind, eflags, key, val in self._elements(inline, 0):
                if kind != "leaf":
                    raise ScanError("inline bucket page is not a leaf")
                yield eflags, key, val
            return
        stack = [root_pgid]
        visited = 0
        while stack:
            pgid = stack.pop()
            visited += 1
            if visited > MAX_PAGES_VISITED:
                raise ScanError("too many pages visited (corrupt tree?)")
            for kind, a, key, val in self._elements(self.data, self._page(pgid)):
                if kind == "branch":
                    stack.append(a)
                else:
                    yield a, key, val

    def bucket(self, name):
        for eflags, key, val in self.walk(self.root):
            if key == name and eflags & BUCKET_LEAF_FLAG:
                root = struct.unpack_from("<Q", val, 0)[0]
                return root, (val[16:] if root == 0 else None)
        raise ScanError("bucket %r not found" % name)


def revision_main(revkey):
    # etcd revision key: 8-byte big-endian main rev, '_', 8-byte sub rev, optional 't' (tombstone)
    if len(revkey) < 17:
        return 0, False
    return struct.unpack_from(">Q", revkey, 0)[0], revkey.endswith(b"t") and len(revkey) == 18


def scan(data, marker=None, canary=None):
    bolt = Bolt(data)
    root, inline = bolt.bucket(b"key")
    history = Counter()
    newest = {}           # key -> (main_rev, class)
    for _, revkey, val in bolt.walk(root, inline):
        try:
            key, value = parse_keyvalue(val)
        except ScanError:
            continue
        if not key.startswith(SECRETS_PREFIX):
            continue
        rev, tomb = revision_main(revkey)
        cls = "deleted" if tomb else classify(value)
        history[cls] += 1
        if key not in newest or rev >= newest[key][0]:
            newest[key] = (rev, cls)
    live = Counter(cls for _, cls in newest.values() if cls != "deleted")
    plaintext_names = sorted(k[len(SECRETS_PREFIX):].decode("utf-8", "replace")
                             for k, (_, cls) in newest.items() if cls in ("plaintext", "unknown"))
    result = {
        "page_size": bolt.page_size,
        "live": live,
        "history": history,
        "plaintext_names": plaintext_names,
        "raw_plaintext_secret_objects": data.count(PLAINTEXT_SECRET_MAGIC),
        "raw_marker": data.count(marker.encode()) if marker else None,
        "canary": None,
    }
    if canary:
        ck = SECRETS_PREFIX + canary.encode()
        result["canary"] = newest[ck][1] if ck in newest else "absent"
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("snapshot")
    ap.add_argument("--expect", choices=("encrypted", "plaintext"))
    ap.add_argument("--strict-raw", action="store_true")
    ap.add_argument("--marker")
    ap.add_argument("--canary", help="NAMESPACE/NAME of a canary Secret that must be encrypted")
    ap.add_argument("--names", action="store_true", help="list plaintext Secret names (ns/name)")
    a = ap.parse_args(argv)
    try:
        with open(a.snapshot, "rb") as f:
            data = f.read()
        r = scan(data, a.marker, a.canary)
    except (OSError, ScanError, struct.error) as e:
        print("etcd-secrets-scan: ERROR: %s" % e, file=sys.stderr)
        return 2

    live, hist = r["live"], r["history"]
    n_live = sum(live.values())
    n_enc = sum(live[c] for c in ENCRYPTED)
    n_plain = live["plaintext"] + live["unknown"]
    print("snapshot:            %s (bbolt page size %d)" % (a.snapshot, r["page_size"]))
    print("live secrets:        %d  encrypted %d (%s)  plaintext %d" % (
        n_live, n_enc, ", ".join("%s %d" % (c, live[c]) for c in ENCRYPTED if live[c]) or "-", n_plain))
    print("revisions in keyspace: %s" % (", ".join("%s %d" % kv for kv in sorted(hist.items())) or "none"))
    print("raw plaintext Secret objects (whole file, free pages included): %d" % r["raw_plaintext_secret_objects"])
    if a.marker:
        print("raw canary marker occurrences: %d" % r["raw_marker"])
    if a.canary:
        print("canary %s: %s" % (a.canary, r["canary"]))
    if a.names and r["plaintext_names"]:
        print("plaintext secrets: " + " ".join(r["plaintext_names"]))

    ok = True
    if a.expect == "encrypted":
        if n_plain:
            print("FAIL: %d live Secret(s) are not encrypted" % n_plain)
            ok = False
        if a.canary and r["canary"] not in ENCRYPTED:
            print("FAIL: canary %s is %s, not encrypted" % (a.canary, r["canary"]))
            ok = False
        if a.marker and r["raw_marker"]:
            print("FAIL: the canary marker appears in plain bytes %d time(s)" % r["raw_marker"])
            ok = False
        if a.strict_raw and r["raw_plaintext_secret_objects"]:
            print("FAIL: %d plaintext Secret object(s) remain in the file (free pages: wait for "
                  "compaction, restart k3s to defragment, snapshot again)" % r["raw_plaintext_secret_objects"])
            ok = False
    elif a.expect == "plaintext":
        if n_enc:
            print("FAIL: %d live Secret(s) are still encrypted" % n_enc)
            ok = False
    if a.expect:
        print("RESULT: %s (expect %s%s)" % ("PASS" if ok else "FAIL", a.expect, ", strict raw" if a.strict_raw else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
