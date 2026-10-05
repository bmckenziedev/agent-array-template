#!/usr/bin/env python3
"""Bounded engine preflight for a synthetic or approved organisation snapshot."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from factory_engine.cli import main as engine_main


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--lanes")
    args = parser.parse_args(argv)
    command = ["submit", "--snapshot", args.snapshot, "--template", args.template, "--dry-run", "--json"]
    if args.lanes:
        command += ["--lanes", args.lanes]
    return engine_main(command)


if __name__ == "__main__":
    raise SystemExit(main())
