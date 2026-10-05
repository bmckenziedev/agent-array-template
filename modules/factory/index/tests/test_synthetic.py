from pathlib import Path
from estate_index.build import build_index, open_ro
from estate_index import queries, reposmd, pack
from estate_index.tokens import default_counter
from estate_index.resolve import Resolver, Package

SNAP = Path(__file__).parent / "fixtures/mini/synthetic"


def test_new_estate(tmp_path):
    db = tmp_path / "index.sqlite"
    meta = build_index(SNAP, db, default_counter())
    assert meta["counts"]["repos"] == 3
    conn = open_ro(db)
    try:
        result = queries.dependents_of(conn, "@example-org/math")
        assert "report" in result["consumer_repos"]
        assert "ui" in queries.impact(conn, "math")["affected_runtime"]
        assert "Blast radius" in reposmd.generate(conn)
        cards = pack.pack_dry_run(conn, default_counter(), SNAP, tmp_path / "packs", template={
            "template": 1, "template_id": "doc-synthetic", "kind": "doc_map",
            "expand": {"via": "find_undocumented", "repo": "math"}})
        assert cards["summary"]["cards"] > 0
    finally:
        conn.close()


def test_arbitrary_scope():
    resolver = Resolver({"lib": {"index.js"}}, [Package("@another-org/lib", "lib", "", main="index.js")])
    assert resolver.resolve("app", "src/a.js", "@another-org/lib").kind == "internal"
