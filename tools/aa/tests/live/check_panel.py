#!/usr/bin/env python3
"""Manual OIDC portal check plan, isolated from offline collection."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

AA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AA_ROOT))
from aa_cli import config


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute and Path(args.kubeconfig).resolve() != config.kubeconfig().resolve():
        parser.error("--kubeconfig must be the private OIDC context written by aa login")
    command = [sys.executable, str(AA_ROOT / "aa.py"), "get", args.task, "--out", args.out]
    if not args.execute:
        print("Dry run:", command)
        print("Use a completed synthetic task owned by the test user; repeat as another user to check denial.")
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
