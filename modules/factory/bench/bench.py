#!/usr/bin/env python3
"""Run synthetic selftests or gate an org-authored unit and candidate."""
import argparse
import json
from pathlib import Path

from harness.gates import gate, selftest
from harness.runner import run
from harness.profile import build


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("selftest")
    check = commands.add_parser("gate")
    check.add_argument("unit", type=Path)
    check.add_argument("candidate", type=Path)
    benchmark = commands.add_parser("run")
    benchmark.add_argument("units", type=Path)
    benchmark.add_argument("model", type=Path)
    benchmark.add_argument("output", type=Path)
    benchmark.add_argument("--token-file", type=Path)
    profile = commands.add_parser("profile")
    profile.add_argument("snapshot", type=Path)
    profile.add_argument("units", type=Path)
    profile.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "profile":
        result = build(args.snapshot, args.units, args.output)
        print(json.dumps({"units": len(result["units"]), "files": len(result["snapshot_files"])}))
        return 0
    if args.command == "selftest":
        result = selftest()
        print(json.dumps(result))
        return int(result["failed"] > 0)
    if args.command == "run":
        token = args.token_file.read_text().strip() if args.token_file else ""
        units = json.loads(args.units.read_text())
        if isinstance(units, dict):
            units = units["units"]
        result = run(units, json.loads(args.model.read_text()), args.output, token)
        print(json.dumps(result))
        return int(result["fail"] > 0 or result["skip"] > 0)
    result = gate(json.loads(args.unit.read_text()), args.candidate.read_text())
    print(result)
    return int(result != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
