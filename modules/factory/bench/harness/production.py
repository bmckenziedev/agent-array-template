"""Use the factory's production comment-only documentation verifier."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ENGINE_JS = Path(__file__).resolve().parents[2] / "engine/js"


def doc_gate(unit: dict, source: Path, output: str) -> str:
    if not shutil.which("node") or not (ENGINE_JS / "node_modules/typescript").exists():
        return "skip: Node and engine JS dependencies required for production doc gates"
    with tempfile.TemporaryDirectory() as scratch:
        base = source
        for _ in Path(unit["target"]).parts:
            base = base.parent
        request = {
            "kind": "doc_map", "unit": {
                "unit_id": unit.get("id", "bench-doc"),
                "target": {"file": unit["target"], "symbols": unit["exports"]},
            }, "code": output,
            "profile": unit.get("profile", {"tsc": {"include": ["**/*.cjs", "**/*.js"]}}),
        }
        result = subprocess.run([
            "node", str(ENGINE_JS / "gate.js"), "--base", str(base),
            "--tools", str(ENGINE_JS), "--scratch", scratch,
        ], input=json.dumps(request), capture_output=True, text=True, timeout=200)
        if result.returncode:
            return "gate_env"
        response = json.loads(result.stdout)
        return "pass" if response.get("ok") else response.get("stage", "gate_env")
