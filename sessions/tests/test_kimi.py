"""Expose the ported offline Kimi wrapper suite to component discovery."""

import importlib.machinery
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "kimi/image/tests/test_kimi_offline.py"
loader = importlib.machinery.SourceFileLoader("kimi_offline", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromModule(module)
