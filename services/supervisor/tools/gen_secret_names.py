"""Generate names only; never read Secret values."""
import argparse
import json
import re
from pathlib import Path


def names(root):
    result = set()
    for path in sorted(Path(root).rglob("secrets.required.yaml")):
        block_indent = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if block_indent is not None and line.strip() and not line.lstrip().startswith("#"):
                indent = len(line) - len(line.lstrip())
                item = re.fullmatch(r"\s*-\s*([^#]+?)(?:\s+#.*)?", line)
                if item and indent >= block_indent:
                    result.add(item[1].strip().strip("\"' "))
                    continue
                block_indent = None
            start = re.fullmatch(r"(\s*)keys:\s*(?:#.*)?", line)
            if start:
                block_indent = len(start[1])
                continue
            match = re.search(r"^\s*keys:\s*\[([^]]*)\]", line)
            if match:
                result.update(v.strip().strip("\"' ") for v in match[1].split(",") if v.strip())
    return sorted(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "aa_supervisor/secret_key_names.json"
    destination.write_text(json.dumps(names(args.root), indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
