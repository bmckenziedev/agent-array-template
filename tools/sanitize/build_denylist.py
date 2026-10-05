#!/usr/bin/env python3
"""Build a hash-only identifier inventory; inputs remain outside the export."""
import argparse
import hashlib
import hmac
import json
import secrets
from collections import defaultdict
from pathlib import Path

EXTRAS = ["17" + ":30", "22" + ":30", "17" + ":25", "utc-" + "07:00",
          "kimi " + str(10 ** 2), "chatgpt " + str(10 ** 2)]

def build(policy, salt):
    atoms = {}
    for rule in policy["rules"]:
        if rule["severity"] == "fail":
            for atom in rule.get("atoms", []):
                atoms.setdefault(atom.lower(), rule["kind"])
    for atom in policy.get("extra_atoms", []) + EXTRAS:
        atoms.setdefault(atom.lower(), "pii")
    counts = defaultdict(int)
    entries = []
    for atom, kind in sorted(atoms.items()):
        counts[kind] += 1
        entries.append({"h": hmac.new(bytes.fromhex(salt), atom.encode(), hashlib.sha256).hexdigest(),
                        "severity": "fail", "label": f"{kind}-{counts[kind]:02d}"})
    return {"version": 1, "algo": "hmac-sha256", "salt": salt, "entries": entries}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifiers", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--new-salt", action="store_true")
    parser.add_argument("--extra-atom", nargs="+", action="extend", default=[])
    args = parser.parse_args(argv)
    # Validate the inventory input without copying its identifying fields.
    json.loads(args.identifiers.read_text(encoding="utf-8-sig"))
    policy = json.loads(args.policy.read_text(encoding="utf-8-sig"))
    policy["extra_atoms"] = policy.get("extra_atoms", []) + args.extra_atom
    salt = (secrets.token_hex(32) if args.new_salt or not args.out.exists()
            else json.loads(args.out.read_text())["salt"])
    args.out.write_text(json.dumps(build(policy, salt), indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
