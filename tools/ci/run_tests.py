#!/usr/bin/env python3
"""Discover and execute isolated offline component suites."""
import argparse
import fnmatch
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

EXCLUDED = {".git", "fixtures", ".ci-venvs", "node_modules", "rendered", "live", "__pycache__"}

def discover(root):
    suites = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        tests = Path(directory)
        if tests.name != "tests":
            continue
        python_files = [f for f in tests.rglob("test_*.py")
                        if not (set(f.relative_to(tests).parts) & EXCLUDED)]
        if python_files:
            suites.append((tests.parent.relative_to(root).as_posix(), "python", tests))
        for script in sorted(tests.glob("test-*.sh")):
            suites.append((script.relative_to(root).as_posix(), "shell", script))
    return sorted(suites)

def python_command(interpreter, tests):
    pytest = subprocess.run([str(interpreter), "-c", "import pytest"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if pytest:
        return [str(interpreter), "-m", "pytest", "-q", "tests", "-p", "no:cacheprovider",
                "--import-mode=importlib", "--ignore=tests/live", "--ignore-glob=*/fixtures/*"]
    return [str(interpreter), "-m", "unittest", "discover", "-s", "tests", "-t", "."]


def execute(root, suite):
    name, kind, path = suite
    if kind == "shell":
        bash = shutil.which("bash")
        if not bash:
            return "SKIP", "bash absent"
        return ("PASS" if subprocess.run([bash, str(path)], cwd=root).returncode == 0 else "FAIL"), ""
    component = path.parent
    with tempfile.TemporaryDirectory(prefix="aa-ci-venv-") as temporary:
        interpreter = Path(sys.executable)
        requirements = [component / name for name in ("requirements.txt", "requirements-test.txt")
                        if (component / name).exists()]
        if requirements:
            environment = Path(temporary)
            # The invoking environment supplies pip; minimal distro Python may omit ensurepip.
            venv.EnvBuilder(with_pip=False).create(environment)
            interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            for requirement in requirements:
                command = [sys.executable, "-m", "pip", "--python", str(interpreter),
                           "install", "--retries", "0", "--timeout", "15"]
                if "--hash=" in requirement.read_text():
                    command.append("--require-hashes")
                result = subprocess.run(command + ["-r", str(requirement)], cwd=component,
                                        capture_output=True, text=True)
                if result.returncode:
                    detail = result.stdout + result.stderr
                    network = any(word in detail.lower() for word in (
                        "connection", "network is unreachable", "name resolution", "timed out",
                        "proxyerror", "getaddrinfo", "temporary failure"))
                    if network and os.environ.get("CI", "").lower() != "true":
                        return "SKIP", "dependency network unavailable"
                    print(detail)
                    return "FAIL", "dependency installation failed"
        status = subprocess.run(python_command(interpreter, path), cwd=component).returncode
        return ("PASS" if status == 0 else "FAIL"), ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", default="*")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    suites = [s for s in discover(root) if fnmatch.fnmatchcase(s[0], args.only)]
    if args.list:
        for name, kind, _ in suites:
            print(f"{name}\t{kind}")
        return 0
    results = []
    for suite in suites:
        try:
            status, detail = execute(root, suite)
        except (OSError, subprocess.SubprocessError) as error:
            status, detail = "FAIL", str(error)
        results.append((suite[0], status, detail))
    print("\nSuite | Result | Detail\n--- | --- | ---")
    for name, status, detail in results:
        print(f"{name} | {status} | {detail}")
    return int(any(status == "FAIL" for _, status, _ in results))

if __name__ == "__main__":
    raise SystemExit(main())
