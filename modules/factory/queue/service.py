"""Workstation result publication adapter; no listener or autonomous feeder."""
import json
import os
from pathlib import Path
WORK = Path(os.environ.get("FACTORY_WORK", "/work"))

def publish(queue):
    result = {"batches": queue.rows()}
    target = queue.home / "results.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result) + "\n", encoding="utf-8")
    temporary.replace(target)
    return result
