"""Token counting with an explicitly supplied model-matched tokenizer.

The fallback chars/3 estimate is approximate, not a guaranteed upper bound.
Do not reuse a Qwen2.5 tokenizer for the GPU v2 models; validate a matching
artifact per model before treating counts as exact. counted_by records provenance.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path


class Counter:
    def __init__(self, tokenizer_json: Path | None = None):
        self.exact = False
        self.tok = None
        self.counted_by = "heuristic:chars/3.0"
        path = tokenizer_json or (Path(os.environ["FACTORY_TOKENIZER"]) if os.environ.get("FACTORY_TOKENIZER") else None)
        if path and Path(path).exists():
            try:
                from tokenizers import Tokenizer  # type: ignore
                self.tok = Tokenizer.from_file(str(path))
                sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
                self.counted_by = f"tokenizer@{sha}"
                self.exact = True
            except Exception:  # noqa: BLE001 - wheel missing or file unreadable: estimate instead
                self.tok = None

    def count(self, text: str) -> int:
        if self.tok is not None:
            return len(self.tok.encode(text, add_special_tokens=False).ids)
        return int(math.ceil(len(text) / 3.0))
