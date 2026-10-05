#!/usr/bin/env python3
"""Check local Markdown destinations and GitHub heading anchors offline."""
import argparse
import hashlib
import hmac
import json
import sys
from collections import Counter
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

SKIP = {".git", "rendered", ".ci-venvs", "node_modules", "fixtures"}
PERSONAL = re.compile(r"(?i)(?:[a-z]:[\\/]+Users[\\/]|/c/Users/)")

def anchors(text):
    seen = Counter()
    result = set()
    fenced = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        if fenced:
            continue
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
        value = heading.group(1) if heading else None
        if index + 1 < len(lines) and re.match(r"^ {0,3}(?:=+|-+)\s*$", lines[index + 1]):
            value = line.strip()
        if value is not None:
            value = re.sub(r"<[^>]*>", "", value)
            value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
            slug = ''.join(c for c in value.lower() if c.isalnum() or c in " _-").replace(" ", "-")
            count = seen[slug]
            seen[slug] += 1
            result.add(slug + (f"-{count}" if count else ""))
    result.update(re.findall(r'<(?:a|h[1-6])\b[^>]*(?:id|name)=["\']([^"\']+)', text))
    return result

def destinations(text):
    # Ignore fenced examples and inline code, which are not rendered links.
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", text)
    text = re.sub(r"`[^`\n]*`", "", text)
    refs = dict((key.lower(), value) for key, value in re.findall(
        r'^ {0,3}\[([^]]+)\]:\s*<?([^\s>]+)>?', text, re.M))
    for hit in re.finditer(r'!?\[[^]]*\]\(\s*(<[^>]+>|[^\s]+?)(?:\s+["\'][^\n]*["\'])?\s*\)', text):
        yield text.count("\n", 0, hit.start()) + 1, hit.group(1).strip("<>")
    for hit in re.finditer(r'!?\[([^]]+)\](?:\[([^]]*)\])?', text):
        key = (hit.group(2) or hit.group(1)).lower()
        if key in refs:
            yield text.count("\n", 0, hit.start()) + 1, refs[key]
    for value in refs.values():
        yield 1, value
    for hit in re.finditer(r'<((?:https?://|file://)[^>]+)>', text):
        yield text.count("\n", 0, hit.start()) + 1, hit.group(1)


def identifier_checker():
    # Reuse the hash inventory to reject original-source URLs without shipping names.
    directory = Path(__file__).resolve().parents[1] / "sanitize"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(directory))
    from atoms import candidates
    denylist = json.loads((directory / "denylist.json").read_text(encoding="utf-8"))
    salt = bytes.fromhex(denylist["salt"])
    hashes = {entry["h"] for entry in denylist["entries"] if entry["severity"] == "fail"}

    def identifies(destination):
        return any(hmac.new(salt, value.encode(), hashlib.sha256).hexdigest() in hashes
                   for value in candidates(unquote(destination)))

    return identifies

def check(root):
    root = root.resolve()
    errors = []
    identifies = identifier_checker()
    for file in sorted(root.rglob("*.md")):
        if set(file.relative_to(root).parts) & SKIP:
            continue
        text = file.read_text(encoding="utf-8")
        for hit in PERSONAL.finditer(text):
            errors.append((file.relative_to(root).as_posix(), text.count("\n", 0, hit.start()) + 1,
                           "personal-path"))
        for line, destination in destinations(text):
            parsed = urlsplit(destination.replace("\\", "/"))
            if PERSONAL.search(unquote(destination)) or parsed.scheme == "file":
                errors.append((file.relative_to(root).as_posix(), line, "personal-path"))
                continue
            if identifies(destination):
                errors.append((file.relative_to(root).as_posix(), line, "source-identifier"))
                continue
            if parsed.scheme or parsed.netloc:
                continue
            target = (file.parent / unquote(parsed.path)).resolve() if parsed.path else file
            if not target.is_relative_to(root):
                reason = "outside-repository"
            elif not target.exists():
                reason = "missing-target"
            elif parsed.fragment and target.suffix.lower() == ".md" and unquote(parsed.fragment) not in anchors(
                    target.read_text(encoding="utf-8")):
                reason = "missing-anchor"
            else:
                continue
            errors.append((file.relative_to(root).as_posix(), line, reason))
    return sorted(set(errors))

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path('.'))
    args = parser.parse_args(argv)
    errors = check(args.root)
    for path, line, reason in errors:
        print(f"FAIL {path}:{line} {reason}")
    print(f"linkcheck: {len(errors)} fail")
    return int(bool(errors))

if __name__ == "__main__":
    raise SystemExit(main())
