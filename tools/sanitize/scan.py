#!/usr/bin/env python3
"""Scan text and forbidden artifacts without disclosing matched identifiers."""
import argparse
import fnmatch
import hashlib
import hmac
import json
import os
import re
from collections import Counter
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from atoms import candidates

HERE = Path(__file__).resolve().parent
SKIP = {".git", "rendered", ".ci-venvs", "node_modules"}

def matches(path, pattern):
    return fnmatch.fnmatchcase(path, pattern) or (pattern.startswith("**/") and
                                                fnmatch.fnmatchcase(path, pattern[3:]))


def sealed_payload(text):
    """Recognize long mapping values without needing a YAML dependency."""
    active = False
    parent_indent = 0
    accumulated = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if re.match(r"encryptedData\s*:", stripped):
            active = True
            parent_indent = indent
            inline = stripped.split(":", 1)[1].strip()
            if len(inline.strip("{}\"'")) > 100:
                return True
            continue
        if active and indent <= parent_indent:
            active = False
        if not active:
            continue
        if ":" in stripped:
            accumulated = stripped.split(":", 1)[1].strip().strip("\"'")
        else:
            accumulated += stripped.strip("\"'")
        if len(accumulated) > 100:
            return True
    return False

def scan(root, policy, denylist, allow=(), warn_as_fail=False, export_gate=False):
    lookup = {e["h"]: e for e in denylist["entries"]}
    salt = bytes.fromhex(denylist["salt"])
    compiled = [(r, re.compile(r["regex"])) for r in policy["rules"]]
    findings = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(files):
            file = Path(directory) / name
            path = file.relative_to(root).as_posix()
            if file.is_symlink() or any(matches(path, p) for p in allow):
                continue
            def add(line, label, severity="fail"):
                findings.append({"level": "FAIL" if severity == "fail" or warn_as_fail else "WARN",
                                 "path": path, "line": line, "label": label})
            with file.open("rb") as stream:
                prefix = stream.read(4096)
                binary = b"\0" in prefix
                raw = prefix if binary else prefix + stream.read()
            if binary:
                if not (path.startswith("docs/") and file.suffix.lower() in {".png", ".svg"}):
                    add(1, "forbidden:binary")
                continue
            text = raw.decode("utf-8", errors="replace")
            for pattern in policy["forbidden_paths"]:
                if pattern.endswith("*.sealed.yaml") and not export_gate:
                    continue
                if matches(path, pattern):
                    if pattern.endswith("*.sealed.yaml") and not sealed_payload(text):
                        continue
                    add(1, "forbidden:" + pattern)
            for rule, regex in compiled:
                if rule["id"] == "sealed-ciphertext" and not export_gate:
                    continue
                for hit in regex.finditer(text):
                    add(text.count("\n", 0, hit.start()) + 1, rule["id"], rule["severity"])
            for number, line in enumerate(text.splitlines(), 1):
                hits = set()
                for candidate in candidates(line):
                    digest = hmac.new(salt, candidate.encode(), hashlib.sha256).hexdigest()
                    if digest in lookup:
                        hits.add(digest)
                for digest in sorted(hits):
                    entry = lookup[digest]
                    add(number, "denylist:" + entry["label"], entry["severity"])
    return findings

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--warn-as-fail", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--export-gate", action="store_true")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error("root must be a directory")
    policy = json.loads((HERE / "rules.json").read_text())
    denylist = json.loads((HERE / "denylist.json").read_text())
    allow = [p.strip() for p in (HERE / "allow.txt").read_text().splitlines()
             if p.strip() and not p.startswith("#")]
    findings = scan(args.root, policy, denylist, allow, args.warn_as_fail, args.export_gate)
    counts = Counter(f["label"] for f in findings)
    fails = sum(f["level"] == "FAIL" for f in findings)
    summary = {"fail": fails, "warn": len(findings) - fails, "labels": dict(sorted(counts.items()))}
    if args.json:
        print(json.dumps({"findings": findings, "summary": summary}, sort_keys=True))
    else:
        for f in findings:
            print(f'{f["level"]} {f["path"]}:{f["line"]} {f["label"]}')
        print(f'sanitize: {fails} fail, {len(findings) - fails} warn')
        print("labels: " + json.dumps(summary["labels"], sort_keys=True))
    return int(bool(fails))

if __name__ == "__main__":
    raise SystemExit(main())
