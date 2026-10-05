"""Coherent queue gauges and durable, idempotent factory lifecycle counters.

The ledger survives task retention. Interval union attributes generation lane
occupancy once across concurrent slots; verification and retry delays are excluded.
Power observations require an explicit trusted adapter and are omitted otherwise.
"""

from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time

KINDS = ("doc_map", "test_gen")
OUTCOMES = ("pass", "fail_parse", "fail_apply", "fail_verify", "fail_runtime", "cancelled")
EVENTS = ("accepted", "bounced", "applied")


def union_seconds(intervals):
    end, total = None, 0.0
    for start, finish in sorted(intervals):
        if finish < start:
            raise ValueError("invalid occupancy interval")
        total += max(0.0, finish - max(start, end if end is not None else start))
        end = max(finish, end if end is not None else finish)
    return total


def labels(**values):
    return "{" + ",".join(k + "=" + json.dumps(str(v)) for k, v in values.items()) + "}"


class Metrics:
    def __init__(self, home, lanes, store):
        self.home = Path(home)
        self.lanes = lanes
        self.store = store
        self.lock = threading.RLock()
        self.ledger = sqlite3.connect(self.home / "metrics.sqlite", check_same_thread=False)
        self.ledger.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS records (
              source TEXT PRIMARY KEY, metric TEXT, lane TEXT, node TEXT,
              kind TEXT, outcome TEXT);
            CREATE TABLE IF NOT EXISTS occupancy (
              source TEXT PRIMARY KEY, lane TEXT, node TEXT, start REAL, finish REAL);
        """)
        self.snapshot = None
        self.last_text = "# Factory telemetry unknown until a successful store refresh\n"

    def _partition(self, lane):
        if lane not in self.lanes:
            raise ValueError("attempt references unconfigured lane")
        return lane, self.lanes[lane].get("node", lane)

    def _initial_lane(self, unit, config):
        if unit.get("status") != "queued" and unit.get("lane") in self.lanes:
            return unit["lane"]
        route = config["lanes"]["routing"][unit["kind"]][unit["difficulty"]]
        index = min(int(unit.get("generations") or 0), len(route) - 1)
        return route[index]["lane"]

    def _record(self, source, metric, lane, kind, outcome):
        if kind not in KINDS:
            return
        _, node = self._partition(lane)
        self.ledger.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)",
                            (source, metric, lane, node, kind, outcome))

    def refresh(self, strict=False):
        with self.lock:
            try:
                self.last_text = self._refresh()
            except (sqlite3.Error, OSError, ValueError, KeyError):
                # A failed refresh must not advance freshness or invent empty queues.
                self.ledger.rollback()
                if strict:
                    raise
            return self.last_text

    def flush(self):
        """Retention cannot delete authoritative history before successful ingestion."""
        return self.refresh(strict=True)

    def text(self):
        with self.lock:
            return self.last_text

    def _refresh(self):
        with self.store.lock:
            batch_states = {row["batch_id"][:16]: row["status"] for row in self.store.db.execute(
                "SELECT batch_id,status FROM batches"
            ).fetchall()}
            approved = self.store.db.execute(
                "SELECT batch_id FROM batches WHERE status IN ('ready','running')"
            ).fetchall()
        for batch in approved:
            path = self.home / "tasks" / batch["batch_id"][:16] / "factory.db"
            if not path.exists():
                # A newly approved expansion is unknown until it has a coherent unit store.
                raise ValueError("approved batch preparation is incomplete")
        gauges = defaultdict(lambda: {"queue_depth": 0, "ready": 0, "inflight": 0})
        for lane in self.lanes:
            gauges[self._partition(lane)]
        for path in sorted((self.home / "tasks").glob("*/factory.db")):
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                task = dict(db.execute("SELECT * FROM task LIMIT 1").fetchone())
                config = json.loads(task["config"])
                units = {r["unit_id"]: dict(r) for r in db.execute("SELECT * FROM unit")}
                for unit in units.values():
                    if (task["status"] in ("finished", "cancelled") or
                            batch_states.get(path.parent.name) in ("done", "quarantine", "cancelled") or
                            unit["status"] not in ("queued", "running")):
                        continue
                    lane = self._initial_lane(unit, config)
                    part = self._partition(lane)
                    gauges[part]["queue_depth"] += 1
                    if unit["status"] == "running":
                        gauges[part]["inflight"] += 1
                    else:
                        deps = json.loads(unit.get("deps") or "[]")
                        if (unit.get("not_before") or 0) <= time.time() and all(
                            dep in units and units[dep]["status"] == "accepted" for dep in deps
                        ) and task["status"] != "paused":
                            gauges[part]["ready"] += 1
                for attempt in db.execute("SELECT * FROM attempt WHERE stage IS NOT NULL"):
                    unit = units.get(attempt["unit_id"])
                    if not unit:
                        continue
                    stage = attempt["stage"]
                    outcome = "pass" if attempt["ok"] else (
                        "cancelled" if stage == "cancelled" else
                        "fail_parse" if stage in ("envelope", "parse") else
                        "fail_apply" if stage in ("splice", "apply") else
                        "fail_runtime" if stage in ("jest", "runtime", "test_env", "gate_env") else
                        "fail_verify"
                    )
                    self._record(str(path) + ":attempt:" + str(attempt["id"]),
                                 "attempts", attempt["lane"], unit["kind"], outcome)
                for event in db.execute("SELECT * FROM event ORDER BY id"):
                    detail = json.loads(event["detail"] or "{}")
                    source = str(path) + ":event:" + str(event["id"])
                    if event["type"] == "generation_occupancy":
                        lane, node = self._partition(detail["lane"])
                        start, end = float(detail["start"]), float(detail["end"])
                        if end < start:
                            raise ValueError("invalid occupancy interval")
                        self.ledger.execute("INSERT OR IGNORE INTO occupancy VALUES(?,?,?,?,?)",
                                            (source, lane, node, start, end))
                    unit = units.get(event["unit_id"])
                    event_kind = {"unit_accepted": "accepted", "unit_bounced": "bounced",
                                  "unit_applied": "applied"}.get(event["type"])
                    if unit and event_kind:
                        final = (task["status"] in ("finished", "cancelled", "paused") or
                                 batch_states.get(path.parent.name) in ("done", "quarantine", "cancelled"))
                        if not final or (event_kind in ("accepted", "bounced") and
                                         unit["status"] != event_kind):
                            # Integration may reject a locally accepted unit before final handback.
                            continue
                        lane = detail.get("lane") or self._initial_lane(unit, config)
                        # One terminal lifecycle counter, even when an event is replayed.
                        lifecycle = str(path) + ":unit:" + unit["unit_id"] + ":" + (
                            "applied" if event_kind == "applied" else "terminal")
                        self._record(lifecycle, "units", lane, unit["kind"], event_kind)
        self.ledger.commit()
        self.snapshot = time.time()
        lines = []
        for (lane, node), values in sorted(gauges.items()):
            for name, value in values.items():
                lines.append("aa_factory_" + name + labels(lane=lane, node=node) + " " + str(value))
            lines.append("aa_factory_snapshot_timestamp_seconds" + labels(lane=lane, node=node) +
                         " " + str(self.snapshot))
        counts = defaultdict(int)
        for metric, lane, node, kind, outcome, count in self.ledger.execute(
            "SELECT metric,lane,node,kind,outcome,COUNT(*) FROM records GROUP BY metric,lane,node,kind,outcome"
        ):
            counts[metric, lane, node, kind, outcome] = count
        for lane in sorted(self.lanes):
            _, node = self._partition(lane)
            for kind in KINDS:
                for metric, choices, name in (("attempts", OUTCOMES, "outcome"),
                                               ("units", EVENTS, "event")):
                    for outcome in choices:
                        label = labels(lane=lane, node=node, kind=kind, **{name: outcome})
                        value = counts[metric, lane, node, kind, outcome]
                        lines.append("aa_factory_" + metric + "_total" + label + " " + str(value))
            spans = list(self.ledger.execute("SELECT start,finish FROM occupancy WHERE lane=? AND node=?",
                                             (lane, node)))
            lines.append("aa_factory_gpu_seconds_total" + labels(lane=lane, node=node) +
                         " " + str(union_seconds(spans)))
        header = ["# TYPE aa_factory_" + name + " gauge" for name in
                  ("queue_depth", "ready", "inflight", "snapshot_timestamp_seconds")]
        header += ["# TYPE aa_factory_" + name + " counter" for name in
                   ("attempts_total", "units_total", "gpu_seconds_total")]
        return "\n".join(header + lines) + "\n"

    def close(self):
        with self.lock:
            self.ledger.close()
