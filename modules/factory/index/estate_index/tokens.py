"""Token counts in the units the GPU prompt caps use (configured tokenizer).

Exact when the `tokenizers` wheel is installed and a tokenizer.json is found
($FACTORY_TOKENIZER_JSON or the engine's $FACTORY_TOKENIZER, else
/opt/factory/tokenizer/qwen2.5-coder-tokenizer.json).
Otherwise a conservative chars/3.0 estimate (the same fallback as bench/harness/tokens.py),
and every result says which one was used (`counted_by`).
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

DEFAULT_PATHS = ("/opt/factory/tokenizer/qwen2.5-coder-tokenizer.json",)
HEURISTIC = "heuristic:chars/3.0"


class Counter:
    def __init__(self, tokenizer_json: str | Path | None = None):
        self.tok = None
        self.exact = False
        self.counted_by = HEURISTIC
        path = Path(tokenizer_json) if tokenizer_json else None
        if path is not None and path.is_file():
            try:
                from tokenizers import Tokenizer  # type: ignore
                self.tok = Tokenizer.from_file(str(path))
                self.counted_by = f"qwen2.5-tokenizer@{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}"
                self.exact = True
            except Exception:   # wheel missing or unreadable file: fall back, and say so
                self.tok = None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self.tok is not None:
            return len(self.tok.encode(text, add_special_tokens=False).ids)
        return int(math.ceil(len(text) / 3.0))

    def count_many(self, texts: list[str]) -> list[int]:
        if self.tok is None:
            return [self.count(t) for t in texts]
        out: list[int] = []
        step = 512
        for i in range(0, len(texts), step):
            chunk = texts[i:i + step]
            enc = self.tok.encode_batch([t or "" for t in chunk], add_special_tokens=False)
            out.extend(len(e.ids) if t else 0 for e, t in zip(enc, chunk))
        return out


def default_counter() -> Counter:
    # FACTORY_TOKENIZER is the factory engine's name for the same file; either one works.
    env = os.environ.get("FACTORY_TOKENIZER_JSON") or os.environ.get("FACTORY_TOKENIZER")
    if env:
        return Counter(env)
    for p in DEFAULT_PATHS:
        if Path(p).is_file():
            return Counter(p)
    return Counter(None)
