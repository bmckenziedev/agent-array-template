#!/usr/bin/env python3
"""Seal credentials held only in memory. Exit 0 on success, 2 on invalid input."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import sys
import warnings

ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z")
KEY = re.compile(r"[-._a-zA-Z0-9]+\Z")


def namespace_ref(path: Path, ref: str) -> str:
    # Parse only the canonical block mapping; ambiguous layouts fail closed.
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = {}
    inside = False
    indent = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not inside and stripped == "namespaces:":
            inside = True
            indent = len(line) - len(line.lstrip())
            continue
        if inside:
            current = len(line) - len(line.lstrip())
            if current <= indent:
                break
            match = re.fullmatch(r"\s+([a-z_]+):\s*([\w'-]+|\"[\w-]+\")\s*(?:#.*)?", line)
            if not match or match[1] in entries:
                raise ValueError("namespaces must be an unambiguous scalar block mapping")
            entries[match[1]] = match[2].strip("\"'")
    if ref == "user_prefix" or ref not in entries:
        raise ValueError("unknown or non-concrete namespace reference")
    return entries[ref]


def hidden(label: str) -> str:
    # Never let getpass fall back to an echoed pipe read.
    if not sys.stdin.isatty():
        raise ValueError("secret prompts require a terminal")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass(label + ": ")
    except getpass.GetPassWarning as error:
        raise ValueError("hidden terminal input is unavailable") from error
    if not value or "\n" in value or "\r" in value:
        raise ValueError("credentials must be non-empty single-line values")
    return value


def build_secret(namespace: str, name: str, data: dict, kind="Opaque") -> dict:
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {
        "name": name, "namespace": namespace}, "type": kind,
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in data.items()}}


def seal(secret: dict, cert: str, output: Path) -> None:
    executable = shutil.which("kubeseal")
    if not executable:
        raise ValueError("kubeseal must be installed on PATH")
    args = [executable, "--cert", cert, "--scope", "strict", "--format", "yaml"]
    result = subprocess.run(args, input=json.dumps(secret), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Do not relay tool output on failure: it may contain plaintext input.
    if result.returncode:
        raise ValueError("kubeseal failed; no output written")
    if "encryptedData:" not in result.stdout or "kind: SealedSecret" not in result.stdout:
        raise ValueError("kubeseal returned an unexpected document")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(result.stdout.rstrip() + "\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["generic", "registry"])
    ns = parser.add_mutually_exclusive_group(required=True)
    ns.add_argument("--namespace-ref")
    ns.add_argument("--namespace")
    parser.add_argument("--org", type=Path, default=ROOT / "org/org.yaml")
    parser.add_argument("--name", required=True)
    parser.add_argument("--keys", help="comma-separated Secret data keys")
    parser.add_argument("--registry-host", help="registry hostname; required for registry mode")
    parser.add_argument("--cert", required=True, help="public certificate outside the repository")
    args = parser.parse_args(argv)
    try:
        namespace = args.namespace or namespace_ref(args.org, args.namespace_ref)
        if not NAME.fullmatch(namespace) or not NAME.fullmatch(args.name):
            raise ValueError("namespace and name must be DNS labels")
        if not Path(args.cert).is_file():
            raise ValueError("public certificate is missing")
        if args.mode == "generic":
            keys = (args.keys or "").split(",")
            if not all(KEY.fullmatch(key) for key in keys) or len(set(keys)) != len(keys):
                raise ValueError("--keys requires unique valid Secret keys")
            data = {key: hidden(key) for key in keys}
            secret = build_secret(namespace, args.name, data)
        else:
            host = args.registry_host
            if not host or not re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]+)?", host):
                raise ValueError("--registry-host requires a hostname with optional port")
            if args.keys:
                raise ValueError("--keys is not accepted for registry mode")
            username = hidden("Registry username")
            password = hidden("Registry password or token")
            auth = base64.b64encode((username + ":" + password).encode()).decode()
            config = json.dumps({"auths": {host: {"username": username,
                                "password": password, "auth": auth}}}, sort_keys=True)
            secret = build_secret(namespace, args.name, {".dockerconfigjson": config},
                                  "kubernetes.io/dockerconfigjson")
        output = ROOT / "secrets/k8s" / namespace / (args.name + ".sealed.yaml")
        seal(secret, args.cert, output)
        print("Wrote " + str(output.relative_to(ROOT)))
        return 0
    except (ValueError, OSError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
