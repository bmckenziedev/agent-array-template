"""Exact export candidate generation contract."""
import re
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._@:+/-]*")
SPLIT_RE = re.compile(r"([._@:+/-])")

def candidates(line: str):
    """Tokens, punctuation-joined sub-sequences of tokens, and 2-3 word n-grams (lower case)."""
    low = line.lower()
    words = TOKEN_RE.findall(low)
    for w in words:
        parts = SPLIT_RE.split(w)  # keeps separators at odd indexes
        pieces = parts[0::2][:8]
        seps = parts[1::2]
        for i in range(len(pieces)):
            acc = pieces[i]
            yield acc
            for j in range(i + 1, len(pieces)):
                acc = acc + seps[j - 1] + pieces[j]
                yield acc
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            yield " ".join(words[i:i + n])
