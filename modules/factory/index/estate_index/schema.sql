-- Estate index schema (one SQLite file per snapshot). Read-only after build.
PRAGMA foreign_keys = OFF;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE repos (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, short TEXT, package TEXT, version TEXT, description TEXT,
  commit_sha TEXT, branch TEXT, dirty INTEGER, files INTEGER, code_files INTEGER, test_files INTEGER,
  lines INTEGER, bytes INTEGER, tokens INTEGER, has_jest INTEGER, test_script TEXT
);

-- Every package.json in the snapshot (nested packages too).
CREATE TABLE packages (id INTEGER PRIMARY KEY, repo_id INTEGER, name TEXT, dir TEXT, main TEXT, version TEXT);

CREATE TABLE pkg_deps (
  repo_id INTEGER, package_dir TEXT, dep TEXT, section TEXT, spec TEXT, internal INTEGER, target_repo TEXT,
  pin_ref TEXT, commits_past_pin INTEGER, files_changed INTEGER
);

CREATE TABLE files (
  id INTEGER PRIMARY KEY, repo_id INTEGER, path TEXT, lang TEXT, role TEXT, bytes INTEGER, lines INTEGER,
  parse_errors INTEGER, tokens INTEGER, symbols INTEGER, UNIQUE (repo_id, path)
);

CREATE TABLE symbols (
  id INTEGER PRIMARY KEY, file_id INTEGER, repo_id INTEGER, parent_id INTEGER, name TEXT, qualname TEXT,
  kind TEXT, start_byte INTEGER, end_byte INTEGER, start_line INTEGER, end_line INTEGER, signature TEXT,
  params TEXT, is_async INTEGER, is_static INTEGER, is_generator INTEGER, private INTEGER, exported INTEGER,
  export_names TEXT, jsdoc INTEGER, jsdoc_json TEXT, jsdoc_complete INTEGER, branchiness INTEGER,
  returns_value INTEGER, io TEXT, io_transitive TEXT, loc INTEGER, tokens INTEGER, sig_tokens INTEGER,
  inline_export INTEGER, targetable INTEGER  -- targetable: top-level declaration or class member (a splice point)
);

CREATE TABLE exports (
  id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT, local TEXT, symbol_id INTEGER, spec TEXT,
  imported TEXT, line INTEGER, target_file_id INTEGER   -- the module re-exported from (barrels), if any
);

-- target_kind: relative|internal|external|builtin|missing_repo|unresolved; edge: runtime|test|tooling
CREATE TABLE imports (
  id INTEGER PRIMARY KEY, file_id INTEGER, repo_id INTEGER, spec TEXT, kind TEXT, line INTEGER, local TEXT,
  member TEXT, names TEXT, lazy INTEGER, target_kind TEXT, target_repo TEXT, target_file_id INTEGER,
  package TEXT, dep_section TEXT, edge TEXT
);

CREATE TABLE import_names (import_id INTEGER, imported TEXT, local TEXT, symbol_id INTEGER);

-- via: local|import|this|member|instance (instance = `.name(` calls matched by method name in a file
-- that references the class; n carries the count for those aggregated rows)
CREATE TABLE refs (
  id INTEGER PRIMARY KEY, file_id INTEGER, caller_symbol_id INTEGER, callee_symbol_id INTEGER, kind TEXT,
  via TEXT, line INTEGER, n INTEGER, import_id INTEGER, cross_repo INTEGER, from_test INTEGER
);

CREATE TABLE member_calls (file_id INTEGER, name TEXT, n INTEGER);

-- via: import|jest_mock|reexport|name_match
CREATE TABLE test_links (test_file_id INTEGER, target_file_id INTEGER, via TEXT);

-- evidence: call|new|ref|instance
CREATE TABLE symbol_tests (symbol_id INTEGER, test_file_id INTEGER, evidence TEXT, n INTEGER);

CREATE TABLE symbol_stats (
  symbol_id INTEGER PRIMARY KEY, calls_same_file INTEGER, calls_same_repo INTEGER, calls_cross_repo INTEGER,
  caller_files INTEGER, caller_repos INTEGER, refs_runtime INTEGER, test_refs INTEGER, test_files INTEGER,
  test_level TEXT, name_calls_estate INTEGER
);

CREATE TABLE repo_edges (
  from_repo TEXT, to_repo TEXT, declared_section TEXT, runtime_imports INTEGER, test_imports INTEGER,
  tooling_imports INTEGER, in_snapshot INTEGER, kind TEXT
);
