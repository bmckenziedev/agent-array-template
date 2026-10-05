#!/usr/bin/env python3
"""Manual transfer check plan; no resources are created by this script."""

import argparse
import subprocess
import sys
from pathlib import Path

AA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AA_ROOT))
from aa_cli import config


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--label-prefix", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--estate", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--ws", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    common = ["--tool", args.tool, "--estate", args.estate, "--path", args.path,
              "--ws", args.ws, "-n", args.namespace]
    commands = [["work", "up", *common], ["work", "push", *common],
                ["snapshot", "up", *common], ["work", "get", "--tool", args.tool,
                 "--ws", args.ws, "--out", args.out, "-n", args.namespace]]
    if args.execute and Path(args.kubeconfig).resolve() != config.kubeconfig().resolve():
        parser.error("--kubeconfig must be the private OIDC context written by aa login")
    for command in commands:
        invocation = [sys.executable, str(AA_ROOT / "aa.py"), *command]
        if not args.execute:
            print("Dry run:", invocation)
        elif subprocess.run(invocation, check=False).returncode:
            return 1
    print("Verify the export and confirm the local checkout is unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
