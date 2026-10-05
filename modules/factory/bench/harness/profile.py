"""Build a profile from an explicit immutable snapshot and reviewed unit definitions."""
import hashlib
import json
from pathlib import Path

from .gates import source_path


def build(snapshot: Path, units_file: Path, output: Path) -> dict:
    units = json.loads(units_file.read_text(encoding="utf-8"))
    manifest = {}
    for unit in units:
        unit["repo_root"] = str(snapshot.resolve())
        path = source_path(unit)
        unit["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[unit["target"]] = unit["source_sha256"]
    profile = {"version": 1, "snapshot_files": manifest, "units": units}
    output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8", newline="\n")
    return profile
