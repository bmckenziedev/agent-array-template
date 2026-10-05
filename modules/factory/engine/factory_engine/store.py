"""Results store: one SQLite file per task (<home>/tasks/<task_id>/factory.db), WAL mode.

Tables: task, unit, attempt, event, rejected, worker. The store holds cards, model outputs and gate
verdicts, never prompts (prompts are re-rendered from the snapshot) and never keys. The task dir
is meant to live on tmpfs in the cluster; `factory purge` deletes it.

Unit lifecycle:  queued -> running -> accepted | bounced | cancelled
                 (finish may turn an accepted unit into bounced with class INTEGRATION)
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS task (
  task_id TEXT PRIMARY KEY, created REAL, status TEXT, snapshot_id TEXT, snapshot_dir TEXT,
  config TEXT, paused_reason TEXT, finished REAL, bundle TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS unit (
  unit_id TEXT PRIMARY KEY, task_id TEXT, seq INTEGER, repo TEXT, kind TEXT, difficulty TEXT,
  file TEXT, symbols TEXT, card TEXT, status TEXT, priority INTEGER, created REAL, updated REAL,
  generations INTEGER DEFAULT 0, gpu_s REAL DEFAULT 0, writer TEXT, lane TEXT, model TEXT,
  output TEXT, gate TEXT, bounce_class TEXT, bounce TEXT, template_id TEXT, deps TEXT,
  not_before REAL DEFAULT 0, infra_fails INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS unit_status ON unit(status, priority, seq);
CREATE TABLE IF NOT EXISTS attempt (
  id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id TEXT, gen INTEGER, lane TEXT, model TEXT, writer TEXT,
  stolen INTEGER, temperature REAL, seed INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
  latency_s REAL, stop TEXT, envelope_ok INTEGER, stage TEXT, ok INTEGER, message TEXT, gate_ms INTEGER,
  output TEXT, created REAL);
CREATE INDEX IF NOT EXISTS attempt_unit ON attempt(unit_id, gen);
CREATE TABLE IF NOT EXISTS event (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, unit_id TEXT, type TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS rejected (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id TEXT, code TEXT, message TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS worker (id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, host TEXT, started REAL,
  heartbeat REAL, state TEXT);
"""

TERMINAL = ("accepted", "bounced", "cancelled")


def _j(v) -> str | None:
    return None if v is None else json.dumps(v, separators=(",", ":"))


def _u(v):
    return None if v is None else json.loads(v)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(DDL)
        row = self.db.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        version = int(row["v"]) if row else 1
        if version > SCHEMA_VERSION:
            raise RuntimeError("unsupported future store schema")
        # Additive migration preserves all old outputs and assigns no guessed identity.
        with self.tx():
            for table, columns in {
                "task": ["team_id", "submitted_by", "data_class", "estate_id"],
                "unit": ["team_id"], "event": ["actor", "actor_kind"],
            }.items():
                existing = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in existing:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema',?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self.db.close()

    def tx(self):
        return _Tx(self.db)

    # ------------------------------------------------------------------ task
    def create_task(self, task_id: str, snapshot_id: str | None, snapshot_dir: str, config: dict, *, team_id=None, submitted_by=None, data_class=None, estate_id=None) -> None:
        now = time.time()
        self.db.execute("INSERT INTO task(task_id, created, status, snapshot_id, snapshot_dir, config, updated) "
                        "VALUES(?,?,?,?,?,?,?)", (task_id, now, "open", snapshot_id, snapshot_dir, _j(config), now))

        self.db.execute("UPDATE task SET team_id=?,submitted_by=?,data_class=?,estate_id=? WHERE task_id=?",
                        (team_id, submitted_by, data_class, estate_id, task_id))

    def task(self) -> dict | None:
        r = self.db.execute("SELECT * FROM task LIMIT 1").fetchone()
        if r is None:
            return None
        d = dict(r)
        d["config"] = _u(d["config"])
        return d

    def set_task(self, **fields) -> None:
        fields["updated"] = time.time()
        if "config" in fields:
            fields["config"] = _j(fields["config"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE task SET {cols}", tuple(fields.values()))

    # ------------------------------------------------------------------ units
    def next_seq(self) -> int:
        r = self.db.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM unit").fetchone()
        return int(r["m"]) + 1

    def has_unit(self, unit_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM unit WHERE unit_id=?", (unit_id,)).fetchone() is not None

    def add_unit(self, card: dict, seq: int) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO unit(unit_id, task_id, seq, repo, kind, difficulty, file, symbols, card, status, priority, "
            "created, updated, template_id, deps) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (card["unit_id"], card["task_id"], seq, card["repo"], card["kind"], card["difficulty"],
             card["target"]["file"], _j(card["target"]["symbols"]), _j(card), "queued", int(card.get("priority", 0)),
             now, now, (card.get("provenance") or {}).get("template_id"),
             _j([d["unit"] for d in card.get("deps", [])])))

        task = self.task() or {}
        self.db.execute("UPDATE unit SET team_id=? WHERE unit_id=?", (task.get("team_id"), card["unit_id"]))

    def add_rejected(self, unit_id: str, code: str, message: str) -> None:
        self.db.execute("INSERT INTO rejected(unit_id, code, message, ts) VALUES(?,?,?,?)",
                        (unit_id, code, message[:2000], time.time()))

    def rejected(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT unit_id, code, message, ts FROM rejected ORDER BY id")]

    def _unit(self, r) -> dict:
        d = dict(r)
        for k in ("symbols", "card", "gate", "bounce", "deps"):
            d[k] = _u(d[k])
        return d

    def unit(self, unit_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM unit WHERE unit_id=?", (unit_id,)).fetchone()
        return self._unit(r) if r else None

    def units(self, status: str | tuple | None = None, repo: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM unit", []
        conds = []
        if status:
            st = (status,) if isinstance(status, str) else tuple(status)
            conds.append(f"status IN ({','.join('?' * len(st))})")
            args += list(st)
        if repo:
            conds.append("repo=?")
            args.append(repo)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY seq"
        return [self._unit(r) for r in self.db.execute(q, args)]

    def queued(self, limit: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM unit WHERE status='queued' AND COALESCE(not_before, 0) <= ? "
                               "ORDER BY priority DESC, seq LIMIT ?", (time.time(), limit))
        return [self._unit(r) for r in rows]

    def next_not_before(self) -> float | None:
        r = self.db.execute("SELECT MIN(not_before) AS t FROM unit WHERE status='queued'").fetchone()
        return r["t"] if r and r["t"] is not None else None

    def counts(self) -> dict:
        out = {s: 0 for s in ("queued", "running", "accepted", "bounced", "cancelled")}
        for r in self.db.execute("SELECT status, COUNT(*) AS n FROM unit GROUP BY status"):
            out[r["status"]] = r["n"]
        out["rejected"] = self.db.execute("SELECT COUNT(*) AS n FROM rejected").fetchone()["n"]
        out["total"] = sum(v for k, v in out.items() if k not in ("rejected", "total"))
        return out

    def set_unit(self, unit_id: str, **fields) -> None:
        fields["updated"] = time.time()
        for k in ("gate", "bounce", "card", "symbols"):
            if k in fields:
                fields[k] = _j(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE unit SET {cols} WHERE unit_id=?", (*fields.values(), unit_id))

    def claim(self, unit_id: str) -> bool:
        cur = self.db.execute("UPDATE unit SET status='running', updated=? WHERE unit_id=? AND status='queued'",
                              (time.time(), unit_id))
        return cur.rowcount == 1

    def requeue_running(self) -> int:
        """A worker that died leaves units 'running'; the next worker puts them back in the queue."""
        cur = self.db.execute("UPDATE unit SET status='queued', updated=? WHERE status='running'", (time.time(),))
        return cur.rowcount

    def cancel_queued(self) -> int:
        cur = self.db.execute("UPDATE unit SET status='cancelled', updated=? WHERE status='queued'", (time.time(),))
        return cur.rowcount

    # ------------------------------------------------------------------ attempts / events
    def add_attempt(self, unit_id: str, **a) -> int:
        a["unit_id"] = unit_id
        a["created"] = time.time()
        cols = ", ".join(a)
        cur = self.db.execute(f"INSERT INTO attempt({cols}) VALUES({','.join('?' * len(a))})", tuple(a.values()))
        return int(cur.lastrowid)

    def update_attempt(self, attempt_id: int, **a) -> None:
        cols = ", ".join(f"{k}=?" for k in a)
        self.db.execute(f"UPDATE attempt SET {cols} WHERE id=?", (*a.values(), attempt_id))

    def attempts(self, unit_id: str | None = None) -> list[dict]:
        if unit_id:
            rows = self.db.execute("SELECT * FROM attempt WHERE unit_id=? ORDER BY gen, id", (unit_id,))
        else:
            rows = self.db.execute("SELECT * FROM attempt ORDER BY id")
        return [dict(r) for r in rows]

    def event(self, type_: str, unit_id: str | None = None, *, actor=None, actor_kind="service", **detail) -> None:
        self.db.execute("INSERT INTO event(ts, unit_id, type, detail, actor, actor_kind) VALUES(?,?,?,?,?,?)",
                        (time.time(), unit_id, type_, _j(detail) if detail else None, actor, actor_kind))

    def events(self, since_id: int = 0, limit: int = 500) -> list[dict]:
        rows = self.db.execute("SELECT * FROM event WHERE id>? ORDER BY id LIMIT ?", (since_id, limit))
        return [{**dict(r), "detail": _u(r["detail"])} for r in rows]

    # ------------------------------------------------------------------ worker lease
    def take_lease(self, stale_s: float) -> bool:
        now = time.time()
        with self.tx():
            r = self.db.execute("SELECT * FROM worker WHERE id=1").fetchone()
            if r is not None and r["state"] == "running" and now - (r["heartbeat"] or 0) < stale_s and r["pid"] != os.getpid():
                return False
            self.db.execute("INSERT OR REPLACE INTO worker(id, pid, host, started, heartbeat, state) VALUES(1,?,?,?,?,?)",
                            (os.getpid(), socket.gethostname(), now, now, "running"))
        return True

    def heartbeat(self) -> bool:
        """Refresh the lease; False when this process no longer holds it."""
        cur = self.db.execute("UPDATE worker SET heartbeat=? WHERE id=1 AND pid=? AND state='running'",
                              (time.time(), os.getpid()))
        return cur.rowcount == 1

    def recover_orphans(self, stale_s: float) -> int:
        """Units left 'running' by a worker that died (no live lease) go back to the queue.
        Without this, a crash with an empty queue wedges the task: nothing would ever be launched."""
        with self.tx():     # atomic with take_lease: a worker cannot claim units in between
            if self.worker_alive(stale_s):
                return 0
            return self.requeue_running()

    def release_lease(self, state: str = "exited") -> None:
        self.db.execute("UPDATE worker SET state=?, heartbeat=? WHERE id=1 AND pid=?", (state, time.time(), os.getpid()))

    def worker(self) -> dict | None:
        r = self.db.execute("SELECT * FROM worker WHERE id=1").fetchone()
        return dict(r) if r else None

    def worker_alive(self, stale_s: float) -> bool:
        w = self.worker()
        return bool(w and w["state"] == "running" and time.time() - (w["heartbeat"] or 0) < stale_s)


class _Tx:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, *_):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
