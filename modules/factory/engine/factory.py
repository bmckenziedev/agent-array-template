#!/usr/bin/env python3
"""Entry point for the factory CLI (agent-array factory engine). See README.md."""
import sys
from pathlib import Path

if sys.version_info < (3, 12):
    sys.exit("factory: Python 3.12+ is required")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from factory_engine.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
