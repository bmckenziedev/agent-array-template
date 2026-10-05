#!/usr/bin/env python3
"""etcd-snapshot-check.py -- offline integrity check of an etcd snapshot file.

Usage: etcd-snapshot-check.py [--quiet] SNAPSHOT_FILE

This standard-library checker validates
what `etcdutl snapshot status` does before it opens the database, with the standard library
only. It never touches the live cluster and opens the file read-only.

A snapshot written by `k3s etcd-snapshot save` (etcd's clientv3 snapshot.Save, same as
`etcdctl snapshot save`) is a bbolt database file followed by a 32-byte SHA-256 trailer over
the database bytes. The check passes when:
  1. the trailer equals sha256(file minus trailer)                (download/bit-rot check);
  2. bbolt meta page 0 or 1 is valid: magic 0xED0CDAED, version 2, a sane page size, and
     its FNV-1a 64-bit checksum over the meta struct matches      (header integrity);
  3. the database high-water mark (meta.pgid * pageSize) fits in the file.
Exit 0 = all checks passed, 1 = a check failed, 2 = usage / unreadable file.
Prints the file's full sha256 as well, so it can be compared with MANIFEST.txt.
"""
import hashlib
import struct
import sys

BBOLT_MAGIC = 0xED0CDAED
BBOLT_VERSION = 2
TRAILER = 32
PAGE_HEADER = 16            # bbolt page header: id u64, flags u16, count u16, overflow u32
META_STRUCT_LEN = 56        # bytes of the meta struct that the checksum covers
CHECKSUM_OFFSET = PAGE_HEADER + META_STRUCT_LEN


def fnv1a64(data: bytes) -> int:
    h = 0xCBF29CE484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def parse_meta(page: bytes):
    """Return (ok, info_dict) for one candidate bbolt meta page."""
    if len(page) < CHECKSUM_OFFSET + 8:
        return False, {"error": "page too short"}
    magic, version, page_size, _flags = struct.unpack_from("<IIII", page, PAGE_HEADER)
    root_pgid, _root_seq, freelist, pgid, txid, checksum = struct.unpack_from(
        "<QQQQQQ", page, PAGE_HEADER + 16)
    info = {"magic": magic, "version": version, "page_size": page_size, "root_pgid": root_pgid,
            "freelist": freelist, "pgid": pgid, "txid": txid, "checksum": checksum}
    if magic != BBOLT_MAGIC:
        return False, {**info, "error": "bad magic"}
    if version != BBOLT_VERSION:
        return False, {**info, "error": "unexpected version"}
    if page_size < 1024 or page_size > 65536 or page_size & (page_size - 1):
        return False, {**info, "error": "implausible page size"}
    want = fnv1a64(page[PAGE_HEADER:CHECKSUM_OFFSET])
    if want != checksum:
        return False, {**info, "error": "meta checksum mismatch"}
    return True, info


def main(argv):
    quiet = "--quiet" in argv
    args = [a for a in argv[1:] if a != "--quiet"]
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: etcd-snapshot-check.py [--quiet] SNAPSHOT_FILE", file=sys.stderr)
        return 2
    path = args[0]
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    failures = []
    out = []
    file_sha = hashlib.sha256(data).hexdigest()
    out.append(f"file:        {path}")
    out.append(f"size:        {len(data)} bytes")
    out.append(f"sha256:      {file_sha}")

    if len(data) < 2 * 4096 + TRAILER:
        failures.append("file too small to be an etcd snapshot")
        db = data
    else:
        db, trailer = data[:-TRAILER], data[-TRAILER:]
        if hashlib.sha256(db).digest() == trailer:
            out.append("trailer:     ok (sha256 of the database bytes matches the 32-byte trailer)")
        else:
            failures.append("sha256 trailer does not match the database bytes")

    # bbolt keeps two meta pages (0 and 1); use the valid one with the highest txid, like bbolt.
    page_size_guess = struct.unpack_from("<I", db, PAGE_HEADER + 8)[0] if len(db) >= 32 else 4096
    if page_size_guess < 1024 or page_size_guess > 65536:
        page_size_guess = 4096
    best = None
    for idx in (0, 1):
        start = idx * page_size_guess
        ok, info = parse_meta(db[start:start + page_size_guess])
        if ok and (best is None or info["txid"] > best["txid"]):
            best = info
        elif not ok and not quiet:
            out.append(f"meta[{idx}]:     invalid ({info.get('error')})")
    if best is None:
        failures.append("no valid bbolt meta page (magic/version/checksum)")
    else:
        out.append(f"bbolt meta:  ok (page size {best['page_size']}, txid {best['txid']}, "
                   f"{best['pgid']} pages, root bucket page {best['root_pgid']})")
        hwm = best["pgid"] * best["page_size"]
        if hwm > len(db):
            failures.append(f"high-water mark {hwm} bytes exceeds database size {len(db)}")
        else:
            exact = "exactly" if hwm == len(db) else "within"
            out.append(f"db size:     ok ({hwm} bytes of pages, {exact} the {len(db)}-byte database)")

    if not quiet:
        print("\n".join(out))
    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        print(f"etcd snapshot check FAILED: {path}", file=sys.stderr)
        return 1
    print(f"etcd snapshot check OK: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
