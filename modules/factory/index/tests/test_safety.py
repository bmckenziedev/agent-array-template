"""The index stays inside the snapshot, and instance-method matching ignores calls that cannot be instances."""
import json
import os
import sys

import pytest
import estate_index.build as build_mod
from estate_index.build import build_index, open_ro
from estate_index.jsparse import parse_file




def _snapshot(root, files: dict[str, str]):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _link_dir(link, target) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":            # junctions need no privilege on Windows
        import _winapi
        try:
            _winapi.CreateJunction(str(target), str(link))
            return True
        except OSError:
            return False
    return False


def _link_file(link, target) -> bool:
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False


def test_member_calls_skip_builtin_receivers():
    fx = parse_file(b"console.log('x'); JSON.parse(s); logger.log('y'); this.log(1); a.b.log(2);\n", ".js")
    assert fx.member_calls["log"] == 3 and fx.member_calls["parse"] == 0
    assert fx.member_recv[("log", "logger")] == 1 and ("log", "console") not in fx.member_recv


def test_instance_calls_ignore_builtins_and_import_bindings(tmp_path, counter):
    snap = _snapshot(tmp_path / "s-20261004-000000-0a0b0c", {
        "a/package.json": json.dumps({"name": "@example-org/a", "version": "1.0.0"}),
        "a/src/logger.js": "class Logger {\n  log(m) { return m; }\n  join(x) { return x; }\n}\n"
                           "module.exports = { Logger };\n",
        "a/src/use.js": "const path = require('path');\nconst { Logger } = require('./logger');\n"
                        "function run() {\n  const l = new Logger();\n  l.log('a');\n  console.log('b');\n"
                        "  console.log('c');\n  return path.join('x', 'y');\n}\nmodule.exports = { run };\n",
    })
    out = tmp_path / "idx" / "index.sqlite"
    build_index(snap, out, counter)
    c = open_ro(out)
    try:
        n = dict(c.execute("""SELECT s.qualname, coalesce(sum(r.n), 0) FROM symbols s LEFT JOIN refs r
                              ON r.callee_symbol_id = s.id AND r.via = 'instance' WHERE s.parent_id IS NOT NULL
                              GROUP BY s.qualname""").fetchall())
    finally:
        c.close()
    assert n["Logger.log"] == 1          # l.log(); not the two console.log() calls
    assert n["Logger.join"] == 0         # path.join() is a call on an import binding


def test_links_out_of_the_snapshot_are_not_followed(tmp_path, counter):
    outside = _snapshot(tmp_path / "outside", {"secret.js": "function leakedSecret(k) { return k; }\n"
                                                            "module.exports = { leakedSecret };\n"})
    snap = _snapshot(tmp_path / "snap" / "s-20261004-000000-0d0e0f", {
        "a/package.json": json.dumps({"name": "@example-org/a"}),
        "a/src/ok.js": "function fine(x) { return x; }\nmodule.exports = { fine };\n",
    })
    made = [_link_dir(snap / "a" / "src" / "outdir", outside), _link_dir(snap / "linkedrepo", outside),
            _link_file(snap / "a" / "src" / "leak.js", outside / "secret.js")]
    if not any(made):
        pytest.skip("cannot create a symlink or junction here")
    out = tmp_path / "idx" / "index.sqlite"
    meta = build_index(snap, out, counter)
    c = open_ro(out)
    try:
        names = {r[0] for r in c.execute("SELECT qualname FROM symbols")}
        paths = {r[0] for r in c.execute("SELECT path FROM files")}
        repos = {r[0] for r in c.execute("SELECT name FROM repos")}
    finally:
        c.close()
    assert "fine" in names and "leakedSecret" not in names
    assert not any("outdir" in p or "leak" in p for p in paths) and repos == {"a"}
    assert meta["counts"]["skipped_links"] == sum(made[::2])      # links inside repos (the repo link is not walked)


def test_failed_build_closes_connection_and_removes_temp_db(tmp_path, counter, monkeypatch):
    snap = _snapshot(tmp_path / "snap", {
        "a/package.json": json.dumps({"name": "a"}),
        "a/src/a.js": "function a() { return 1; }\nmodule.exports = { a };\n",
    })
    out = tmp_path / "index" / "index.sqlite"
    original = build_mod.Path.read_text

    def fail_schema(path, *args, **kwargs):
        if path.name == "schema.sql":
            raise RuntimeError("injected after sqlite connect")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(build_mod.Path, "read_text", fail_schema)
    with pytest.raises(RuntimeError, match="injected"):
        build_index(snap, out, counter)
    tmp = out.with_name(out.name + f".tmp-{os.getpid()}")
    assert not tmp.exists()
    monkeypatch.setattr(build_mod.Path, "read_text", original)
    build_index(snap, out, counter)
    assert out.is_file()
