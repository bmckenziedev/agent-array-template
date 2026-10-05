from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from .production import doc_gate

ROOT = Path(__file__).resolve().parents[1]
GATE_IMAGE = "node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c"


def source_path(unit: dict) -> Path:
    root = Path(unit.get("repo_root", ROOT / "examples/toy-repo")).resolve()
    target = Path(unit["target"])
    if target.is_absolute() or ".." in target.parts:
        raise ValueError("target must remain inside repository snapshot")
    path = (root / target).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("target must be a regular snapshot file")
    return path


def gate(unit: dict, output: str) -> str:
    """Return the first failed gate, or pass. Bound outputs before parsing."""
    if not output.strip() or len(output.encode()) > 32768:
        return "envelope"
    try:
        source_file = source_path(unit)
    except (KeyError, ValueError):
        return "snapshot"
    if unit.get("source_sha256") and hashlib.sha256(source_file.read_bytes()).hexdigest() != unit["source_sha256"]:
        return "snapshot"
    if unit["kind"] == "doc_map":
        return doc_gate(unit, source_file, output)
    if "node:assert/strict" not in output or unit["symbol"] not in output:
        return "schema"
    if not shutil.which("docker"):
        return "skip: Docker unavailable; executable gates require isolation"
    probe = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
    if probe.returncode:
        return "skip: Docker daemon unavailable; executable gates require isolation"
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        source = source_file.read_text()
        (work / unit["target"]).parent.mkdir(parents=True, exist_ok=True)
        (work / unit["target"]).write_text(source, encoding="utf-8", newline="\n")
        (work / "candidate.test.cjs").write_text(output, encoding="utf-8", newline="\n")
        def execute() -> bool:
            name = "factory-bench-" + uuid.uuid4().hex
            try:
                result = subprocess.run([
                    "docker", "run", "--rm", "--name", name, "--log-driver=none",
                    "--network=none", "--read-only", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--pids-limit=64",
                    "--memory=128m", "--cpus=1", "--user=65534:65534",
                    "-v", f"{work}:/work:ro", "-w", "/work", GATE_IMAGE,
                    "node", "--test", "candidate.test.cjs",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                return result.returncode == 0
            except subprocess.TimeoutExpired:
                return False
            finally:
                # Killing the Docker client alone does not terminate its container.
                subprocess.run(["docker", "rm", "--force", name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if not execute():
            return "runtime"
        (work / unit["target"]).write_text(source.replace(unit["mutation"][0], unit["mutation"][1]), encoding="utf-8")
        return "mutation" if execute() else "pass"


def selftest() -> dict:
    units = json.loads((ROOT / "examples/units.json").read_text())
    checks = []
    for unit in units:
        if unit["kind"] == "doc_map":
            reference = json.dumps(unit["reference"])
            result = gate(unit, reference)
            checks.append(result == "pass" or result.startswith("skip:"))
            if result.startswith("skip:"):
                print(result)
            else:
                checks.append(gate(unit, reference.replace(unit["exports"][0], "invented")) == "scope")
                checks.append(gate(unit, json.dumps({unit["exports"][0]: "/** TODO */"})) == "hygiene")
        else:
            checks.append(gate(unit, "console.log('no assertions');") == "schema")
            result = gate(unit, unit["reference"])
            checks.append(result == "pass" or result.startswith("skip:"))
            if result.startswith("skip:"):
                print(result)
            else:
                ineffective = (
                    "const assert = require('node:assert/strict');\n"
                    f"const {{{unit['symbol']}}} = require('./{unit['target']}');\n"
                    "assert.equal(1, 1);\n"
                )
                checks.append(gate(unit, ineffective) == "mutation")
                broken = ineffective.replace("assert.equal(1, 1)", "assert.equal(1, 2)")
                checks.append(gate(unit, broken) == "runtime")
    return {"units": len(units), "checks": len(checks), "passed": sum(checks), "failed": checks.count(False)}
