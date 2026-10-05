#!/usr/bin/env python3
"""Offline post-renderer RBAC suite; optionally validate a real pinned Helm render."""
import argparse
import importlib.util
from pathlib import Path
import unittest
import yaml

HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', help='Pinned helm template output, outside the repository')
    args = parser.parse_args(argv)
    spec = importlib.util.spec_from_file_location('monitoring_tests',HERE.parent/'tests/test_monitoring.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(module.PostRenderer))
    if args.render:
        with open(args.render,encoding='utf-8') as stream:
            module.POST.transform([doc for doc in yaml.safe_load_all(stream) if doc])
        print('PASS: actual pinned chart accepted by fail-closed post-renderer')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
