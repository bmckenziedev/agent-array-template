#!/usr/bin/env python3
"""Foreground Docker harness; no cluster, host mounts or retained resources."""

import argparse
import subprocess
from run_pipeline import main as run_pipeline
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    image = "agent-array-portal-e2e:local"
    if not args.no_build:
        subprocess.run(
            [
                "docker",
                "build",
                "-f",
                str(root / "portal/e2e/Dockerfile"),
                "-t",
                image,
                str(root),
            ],
            check=True,
        )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            image,
        ]
    )
    if result.returncode:
        return result.returncode
    return run_pipeline([])


if __name__ == "__main__":
    raise SystemExit(main())
