#!/usr/bin/env python3
"""Re-lock devpi-server's dependency closure in modules/pkg-mirror/k8s/20-devpi.tmpl.yaml.

    python modules/pkg-mirror/lock_devpi.py [DEVPI_SERVER_VERSION] [--check]

Needs Docker (Linux engine) and Python 3.11+ (stdlib only). Steps:
  1. `pip download --only-binary=:all:` devpi-server==VERSION in the SAME
     digest-pinned python image the Deployment runs (read from 20-devpi.yaml), so
     the wheels are exactly the ones pip picks in the pod (CPython 3.12, x86_64).
  2. sha256 every wheel and cross-check it against PyPI's JSON API; any mismatch
     or missing file aborts.
  3. Test-install the new lock in that image with --require-hashes --no-deps and
     run `pip check` (the lock must be the complete, consistent closure).
  4. Rewrite the `requirements.txt: |` block of 20-devpi.yaml (unless --check,
     which only reports whether the YAML already matches).
VERSION defaults to the devpi-server version currently in the YAML (a re-lock of
the same version picks up new releases of its dependencies). After a change:
apply 20-devpi.yaml and `restart the devpi Deployment in the rendered mirror namespace`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

YAML = Path(__file__).resolve().parent / "k8s" / "20-devpi.tmpl.yaml"
BLOCK_RE = re.compile(r"(?ms)^(  requirements\.txt: \|\n)(.*?)(?=^  install\.sh: \|)")
IMAGE_RE = re.compile(r"image: (docker\.io/library/python:[^\s@]+@sha256:[0-9a-f]{64})")


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def docker(*args: str) -> str:
    p = subprocess.run(["docker", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"docker {' '.join(args[:3])}... failed:\n{p.stderr[-3000:]}")
    return p.stdout


def pypi_sha256(name: str, version: str, filename: str) -> str | None:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    return next((u["digests"]["sha256"] for u in data["urls"] if u["filename"] == filename), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--yes", action="store_true", help="allow Docker/network access and lock updates")
    parsed = parser.parse_args(argv)
    if not parsed.yes:
        parser.error("relocking contacts registries and runs Docker; supply --yes")
    args = [parsed.version] if parsed.version else []
    check_only = parsed.check
    text = YAML.read_text(encoding="utf-8")
    m = BLOCK_RE.search(text)
    images = set(IMAGE_RE.findall(text))
    if not m or len(images) != 1:
        raise SystemExit("20-devpi.yaml: requirements block or the single python image not found")
    image = images.pop()
    cur = re.search(r"^\s*devpi-server==(\S+)", m.group(2), re.M)
    version = args[0] if args else (cur.group(1) if cur else None)
    if not version or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
        raise SystemExit("give a devpi-server version")
    print(f"locking devpi-server=={version} in {image}")

    with tempfile.TemporaryDirectory() as td:
        wheels = Path(td) / "wheels"
        wheels.mkdir()
        docker("run", "--rm", "--mount", f"type=bind,source={wheels},target=/out",
               "--entrypoint", "pip", image, "download", "-q", "--disable-pip-version-check",
               "--no-cache-dir", "--only-binary=:all:", "--dest", "/out", f"devpi-server=={version}")
        rows = []
        for f in sorted(wheels.iterdir()):
            if not f.name.endswith(".whl"):
                raise SystemExit(f"not a wheel: {f.name}")
            name, ver = f.name.split("-")[:2]
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            published = pypi_sha256(name, ver, f.name)
            if published != digest:
                raise SystemExit(f"sha256 mismatch vs PyPI JSON for {f.name}: {digest} != {published}")
            rows.append((canon(name), ver, digest))
        lock = "".join(f"{n}=={v} \\\n    --hash=sha256:{h}\n" for n, v, h in sorted(rows))
        (Path(td) / "requirements.txt").write_text(lock, encoding="utf-8", newline="\n")
        print(f"  {len(rows)} wheels, every sha256 matches PyPI's JSON API")
        out = docker("run", "--rm", "--mount", f"type=bind,source={td},target=/lock,readonly",
                     "-e", "PYTHONWARNINGS=ignore:pkg_resources is deprecated",
                     "--entrypoint", "sh", image, "-ec",
                     "python3 -m venv /tmp/v && /tmp/v/bin/pip install -q --no-cache-dir "
                     "--disable-pip-version-check --require-hashes --only-binary=:all: --no-deps "
                     "-r /lock/requirements.txt && /tmp/v/bin/pip check && /tmp/v/bin/devpi-server --version")
        print("  test install + pip check:", out.strip().splitlines()[-1])

    block = "".join("    " + line + "\n" for line in lock.splitlines())
    new = text[:m.start(2)] + block + text[m.end(2):]
    new = re.sub(r"# devpi-server \S+ for CPython", f"# devpi-server {version} for CPython", new)
    if new == text:
        print("20-devpi.yaml is already up to date")
        return 0
    if check_only:
        print("20-devpi.yaml differs from a fresh lock (run without --check to rewrite it)")
        return 1
    YAML.write_text(new, encoding="utf-8", newline="\n")
    print(f"rewrote {YAML.name}; apply it, then: restart the devpi Deployment in the rendered mirror namespace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
