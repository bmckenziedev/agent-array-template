"""Cross-repo estate index for frontier sessions (docs/FACTORY-DESIGN.md, S2 "Index").

Builds one SQLite file per uploaded snapshot (/work/snap/<id>/<repo>/...): symbols with
spans, signatures and JSDoc, exports (module.exports / exports.x / ESM), requires and
imports resolved across repos (@example-org/* -> sibling repo via package.json), call-site
counts, test-coverage hints, a repo dependency / blast-radius graph and a generated
REPOS.md. Parse-only: no file in the snapshot is ever executed or required.
"""

INDEX_VERSION = "3"     # 2: symlinks/junctions skipped; instance calls on builtins/import bindings dropped
                        # 3: a constructor is test_level=direct when a test does `new C()`
