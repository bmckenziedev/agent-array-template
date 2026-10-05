"""estate_index CLI.

  python -m estate_index build SNAPSHOT_DIR [--out INDEX.sqlite] [--repos-md PATH] [--compare REPOS.md]
  python -m estate_index stats INDEX
  python -m estate_index repos-md INDEX [--compare <optional-map>/REPOS.md] [--out PATH]
  python -m estate_index untested INDEX [--repo R] [--group-by module] [--limit N] [--offset N]
  python -m estate_index dependents INDEX TARGET [--name EXPORT]
  python -m estate_index impact INDEX TARGET [--depth N]
  python -m estate_index pack INDEX (--template FILE | --cards FILE) [--limit N]

INDEX defaults to $FACTORY_INDEX_ROOT/<snapshot id>/index.sqlite (FACTORY_INDEX_ROOT=/work/index).
Every subcommand prints JSON (repos-md prints markdown unless --out).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import pack as pack_mod
from . import queries, reposmd
from .build import build_index, open_ro, snapshot_info
from .tokens import Counter, default_counter

INDEX_ROOT = Path(os.environ.get("FACTORY_INDEX_ROOT", "/work/index"))


def _counter(path: str | None) -> Counter:
    return Counter(path) if path else default_counter()


def _dump(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=1, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="estate_index", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="index a snapshot directory")
    b.add_argument("snapshot", type=Path)
    b.add_argument("--out", type=Path)
    b.add_argument("--repos-md", type=Path, help="also write the generated REPOS.md here")
    b.add_argument("--compare", type=Path, help="hand-written REPOS.md to diff against")
    b.add_argument("--tokenizer", help="Qwen tokenizer.json (else $FACTORY_TOKENIZER_JSON)")
    for name in ("stats", "repos-md", "untested", "dependents", "impact", "pack"):
        p = sub.add_parser(name)
        p.add_argument("index", type=Path)
        if name == "repos-md":
            p.add_argument("--compare", type=Path)
            p.add_argument("--out", type=Path)
        if name == "untested":
            p.add_argument("--repo")
            p.add_argument("--group-by", choices=["module"])
            p.add_argument("--limit", type=int, default=20)
            p.add_argument("--offset", type=int, default=0)
            p.add_argument("--min-branchiness", type=int, default=2)
            p.add_argument("--no-skip-io", action="store_true")
        if name in ("dependents", "impact"):
            p.add_argument("target")
            p.add_argument("--repo")
        if name == "dependents":
            p.add_argument("--name")
            p.add_argument("--limit", type=int, default=40)
        if name == "impact":
            p.add_argument("--depth", type=int, default=3)
        if name == "pack":
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--template", type=Path)
            g.add_argument("--cards", type=Path)
            p.add_argument("--limit", type=int, default=20)
            p.add_argument("--tokenizer")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        info = snapshot_info(a.snapshot)
        out = a.out or INDEX_ROOT / info["snapshot_id"] / "index.sqlite"
        meta = build_index(a.snapshot, out, _counter(a.tokenizer))
        md_path = a.repos_md or out.with_name("REPOS.md")
        conn = open_ro(out)
        md_path.write_text(reposmd.generate(conn, a.compare), encoding="utf-8")
        conn.close()
        _dump({"index": str(out), "repos_md": str(md_path), **meta})
        return 0
    conn = open_ro(a.index)
    try:
        if a.cmd == "stats":
            _dump(queries.overview(conn))
        elif a.cmd == "repos-md":
            md = reposmd.generate(conn, a.compare)
            if a.out:
                a.out.write_text(md, encoding="utf-8")
                _dump({"repos_md": str(a.out)})
            else:
                sys.stdout.write(md)
        elif a.cmd == "untested":
            _dump(queries.find_untested(conn, repo=a.repo, limit=a.limit, offset=a.offset, group_by=a.group_by,
                                        min_branchiness=a.min_branchiness, skip_io=not a.no_skip_io))
        elif a.cmd == "dependents":
            _dump(queries.dependents_of(conn, a.target, name=a.name, repo=a.repo, limit=a.limit))
        elif a.cmd == "impact":
            _dump(queries.impact(conn, a.target, repo=a.repo, depth=a.depth))
        elif a.cmd == "pack":
            meta = queries.overview(conn)
            snap = Path(conn.execute("SELECT value FROM meta WHERE key='snapshot_dir'").fetchone()[0])
            req = json.loads((a.template or a.cards).read_text(encoding="utf-8"))
            if a.template and isinstance(req, dict) and isinstance(req.get("profile"), str):
                # an explicitly named local file: inline it the way the engine CLI resolves it (next to the template)
                req["profile"] = json.loads((a.template.parent / req["profile"]).read_text(encoding="utf-8"))
            _dump(pack_mod.pack_dry_run(conn, _counter(a.tokenizer), snap, a.index.parent / "packs",
                                        template=req if a.template else None,
                                        cards=(req.get("cards") if isinstance(req, dict) else req) if a.cards else None,
                                        limit=a.limit))
            del meta
    except (queries.TargetError, pack_mod.PackRequestError) as exc:
        _dump({"error": str(exc)})
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
