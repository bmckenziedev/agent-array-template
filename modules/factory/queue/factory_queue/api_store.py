"""Crash-safe bounded batch store, shared by HTTP and foreground workers."""
import contextlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


def read_snapshot_file(path):
    """Enforce the byte bound before allocating even if the file changes mid-read."""
    maximum = 64 * 1024 * 1024
    if path.stat().st_size > maximum:
        raise ValueError("snapshot file exceeds byte cap")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("snapshot file exceeds byte cap")
    return data


class BatchStore:
    def __init__(self, path, estates, teams, caps=None, snapshot_root=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.state_dir = path.parent
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS batches (
              batch_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, submitted_by TEXT NOT NULL,
              data_class TEXT NOT NULL, estate_id TEXT NOT NULL, status TEXT NOT NULL,
              priority INTEGER NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL,
              result TEXT, lease_until REAL, claim_id TEXT);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, ts REAL NOT NULL, batch_id TEXT NOT NULL,
              event TEXT NOT NULL, actor TEXT NOT NULL, actor_kind TEXT NOT NULL);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(batches)")}
        if "finished" not in columns:
            self.db.execute("ALTER TABLE batches ADD COLUMN finished REAL")
        self.deny_globs = estates.get("deny_globs", []) if isinstance(estates, dict) else []
        self.estates = estates.get("estates", []) if isinstance(estates, dict) else estates
        self.teams = {t["id"]: t for t in teams} if isinstance(teams, list) else teams
        self.caps = {"max_batches": 32, "max_cards": 100, "max_payload_bytes": 1048576, **(caps or {})}
        self.snapshot_root = Path(snapshot_root) if snapshot_root else None
        self.lock = threading.RLock()

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def _event(self, bid, verb, identity, kind):
        self.db.execute("INSERT INTO events(ts,batch_id,event,actor,actor_kind) VALUES(?,?,?,?,?)",
                        (time.time(), bid, verb, identity.get("sub") or identity["user"], kind))

    def _decode(self, row):
        if row is None:
            raise KeyError("batch not found")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def get(self, batch_id):
        with self.lock:
            return self._decode(self.db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone())

    def list(self, team, limit=100):
        with self.lock:
            return [self._decode(r) for r in self.db.execute(
                "SELECT * FROM batches WHERE team_id=? ORDER BY created LIMIT ?", (team, min(max(1, limit), 100)))]

    def submit(self, payload, identity):
        team = payload.get("team") or identity["primary_team"]
        if team not in identity["teams"] or team not in self.teams:
            raise PermissionError("submitting team membership required")
        estates = [e for e in self.estates if e["id"] == payload.get("estate_id") and
                   team in e.get("teams", [e.get("team", e.get("owner_team"))])]
        if not estates:
            raise PermissionError("estate is not allowed for submitting team")
        estate = estates[0]
        encoded = json.dumps(payload, separators=(",", ":"))
        cards = payload.get("cards", [])
        if not isinstance(cards, list) or len(cards) > self.caps["max_cards"]:
            raise ValueError("card cap exceeded")
        if not cards and not isinstance(payload.get("template"), dict):
            raise ValueError("template or cards required")
        if len(encoded.encode()) > self.caps["max_payload_bytes"]:
            raise ValueError("payload cap exceeded")
        if any(key.startswith("_") for key in payload):
            raise ValueError("reserved service fields forbidden")
        self.validate_inputs(payload)
        priority = payload.get("priority", 0)
        if type(priority) is not int:
            raise ValueError("priority must be an integer")
        priority = min(priority, int(self.teams[team].get("factory", {}).get("queue_priority_ceiling", 0)))
        bid = uuid.uuid4().hex
        with self.transaction():
            count = self.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
            if count >= self.caps["max_batches"]:
                raise ValueError("queue backpressure: batch cap exceeded")
            self.db.execute("INSERT INTO batches(batch_id,team_id,submitted_by,data_class,estate_id,status,priority,created,payload,result,lease_until,claim_id) VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                            (bid, team, identity["user"], estate["data_class"], estate["id"],
                             "pending-approval", priority, time.time(), encoded))
            self._event(bid, "batch.submit", identity, "user")
        return self.get(bid)

    @staticmethod
    def validate_inputs(payload):
        from factory_engine.schema import validate_or_raise
        if payload.get("template"):
            validate_or_raise("template.v1", payload["template"])
        for card in payload.get("cards", []):
            validate_or_raise("card.v1", card)
        def inspect(value):
            if isinstance(value, dict):
                for key, val in value.items():
                    if key in ("key_file", "key_env", "api_key", "baseJestConfig", "endpoint", "base_url"):
                        raise ValueError("credential, host configuration or endpoint input forbidden")
                    if key in ("sources", "roots", "include", "exclude") and isinstance(val, list):
                        for name in val:
                            from pathlib import PurePosixPath
                            path = PurePosixPath(name)
                            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                                raise ValueError("unsafe profile path")
                    if key == "file" and isinstance(val, str):
                        from pathlib import PurePosixPath
                        path = PurePosixPath(val)
                        if path.is_absolute() or ".." in path.parts or "\\" in val or ":" in val:
                            raise ValueError("unsafe input file path")
                    if key == "exemplar" and isinstance(val, dict) and "file" in val:
                        raise ValueError("only inline exemplars accepted")
                    inspect(val)
            elif isinstance(value, list):
                for val in value:
                    inspect(val)
        inspect(payload)

    def snapshot_manifest(self, estate_id):
        import hashlib
        if not self.snapshot_root:
            raise ValueError("snapshot root is not configured")
        root = self.snapshot_root.resolve()
        snapshot = root / estate_id
        if snapshot.is_symlink() or root not in snapshot.resolve().parents or not snapshot.is_dir():
            raise ValueError("estate snapshot mount missing")
        files = {}
        total = 0
        for path in sorted(snapshot.rglob("*")):
            if path.is_symlink():
                raise ValueError("snapshot links forbidden")
            if path.is_file():
                name = path.relative_to(snapshot).as_posix()
                if any(part in (".git", "node_modules", ".ssh", ".kube") for part in path.relative_to(snapshot).parts):
                    raise ValueError("snapshot contains excluded directory")
                from .snapshot_queue import safe_path
                safe_path(snapshot, name)
                import fnmatch
                if any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path.name, pattern)
                       for pattern in self.deny_globs):
                    raise ValueError("snapshot includes a denied estate path")
                data = read_snapshot_file(path)
                total += len(data)
                if len(files) >= 10000 or total > 64 * 1024 * 1024:
                    raise ValueError("snapshot exceeds file or byte cap")
                files[name] = hashlib.sha256(data).hexdigest()
        if not files:
            raise ValueError("snapshot is empty")
        return files

    def _authorize(self, batch, identity, lead=False):
        team = batch["team_id"]
        if team not in identity["teams"]:
            raise PermissionError("batch belongs to another team")
        if lead and identity["user"] not in self.teams[team].get("leads", []):
            raise PermissionError("team lead required")

    def approve(self, bid, identity):
        with self.transaction():
            batch = self.get(bid)
            self._authorize(batch, identity, lead=True)
            if batch["status"] != "pending-approval":
                raise ValueError("batch is not pending approval")
            if self.snapshot_root:
                payload = batch["payload"]
                payload["_snapshot_manifest"] = self.snapshot_manifest(batch["estate_id"])
                import hashlib
                import shutil
                destination = self.state_dir / "approved-snapshots" / bid
                if destination.exists():
                    shutil.rmtree(destination)
                source = self.snapshot_root.resolve() / batch["estate_id"]
                for name, expected in payload["_snapshot_manifest"].items():
                    path = source / name
                    if any(p.is_symlink() for p in (path, *path.parents)):
                        raise ValueError("snapshot link detected during approval")
                    data = read_snapshot_file(path)
                    if hashlib.sha256(data).hexdigest() != expected:
                        raise ValueError("snapshot changed during approval")
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                payload["_snapshot_dir"] = str(destination.resolve())
                self.db.execute("UPDATE batches SET payload=? WHERE batch_id=?", (json.dumps(payload), bid))
            self.db.execute("UPDATE batches SET status='ready' WHERE batch_id=?", (bid,))
            self._event(bid, "batch.approve", identity, "lead")
        return self.get(bid)

    def cancel(self, bid, identity):
        with self.transaction():
            batch = self.get(bid)
            self._authorize(batch, identity)
            if identity["user"] != batch["submitted_by"]:
                self._authorize(batch, identity, lead=True)
            if batch["status"] in ("done", "cancelled", "quarantine"):
                raise ValueError("batch is terminal")
            self.db.execute("UPDATE batches SET status='cancelled',claim_id=NULL,finished=? WHERE batch_id=?", (time.time(), bid))
            kind = "lead" if identity["user"] in self.teams[batch["team_id"]].get("leads", []) else "user"
            self._event(bid, "batch.cancel", identity, kind)
        return self.get(bid)

    def recover(self):
        with self.transaction():
            self.db.execute("UPDATE batches SET status='ready', claim_id=NULL WHERE status='running' AND lease_until<?", (time.time(),))

    def claim(self, batch_id, seconds=900):
        with self.transaction():
            claim_id = uuid.uuid4().hex
            changed = self.db.execute("UPDATE batches SET status='running',lease_until=?,claim_id=? WHERE batch_id=? AND status='ready'",
                                      (time.time() + min(seconds, 900), claim_id, batch_id)).rowcount
            if not changed:
                return None
            self._event(batch_id, "batch.claim", {"user": "factory-service"}, "service")
        return self.get(batch_id)

    def complete(self, bid, claim_id, result, status="done"):
        if status not in ("done", "quarantine"):
            raise ValueError("invalid completion status")
        with self.transaction():
            changed = self.db.execute("UPDATE batches SET status=?,result=?,claim_id=NULL,finished=? WHERE batch_id=? AND status='running' AND claim_id=?",
                                      (status, json.dumps(result), time.time(), bid, claim_id)).rowcount
            if changed:
                self._event(bid, "batch." + status, {"user": "factory-service"}, "service")
        return bool(changed)

    def events(self, bid):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM events WHERE batch_id=? ORDER BY id LIMIT 500", (bid,))]

    def prune(self, retention_seconds=43200, *, engine_home=None, exclude=()):
        """Remove terminal payloads and pinned bytes; durable audit events remain."""
        import shutil
        if not 1 <= retention_seconds <= 604800:
            raise ValueError("retention must be bounded to one week")
        removed = 0
        with self.transaction():
            rows = self.db.execute("SELECT batch_id FROM batches WHERE status IN ('done','cancelled','quarantine') AND finished<?",
                                   (time.time() - retention_seconds,)).fetchall()
            for row in rows:
                bid = row["batch_id"]
                if bid in exclude:
                    continue
                root = (self.state_dir / "approved-snapshots").resolve()
                target = (root / bid).resolve()
                if root not in target.parents:
                    raise ValueError("invalid snapshot cleanup boundary")
                if target.exists():
                    shutil.rmtree(target)
                if engine_home:
                    for base, name in ((Path(engine_home) / "tasks", bid[:16]),
                                       (Path(engine_home) / "inputs", bid)):
                        boundary = base.resolve()
                        target = (boundary / name).resolve()
                        if boundary not in target.parents:
                            raise ValueError("invalid task cleanup boundary")
                        if target.exists():
                            shutil.rmtree(target)
                self.db.execute("DELETE FROM batches WHERE batch_id=?", (bid,))
                removed += 1
        return removed

    def close(self):
        with self.lock:
            self.db.close()
