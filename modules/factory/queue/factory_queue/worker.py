"""Supervised foreground engine loop; all teams share lane admission.

Snapshots are immutable administrator-mounted estate directories. Missing mounts
fail closed. Each approved batch is packed once before any unit becomes runnable.
"""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import time
from factory_engine import bundle, cli, config
from factory_engine.engine import Engine
from factory_engine.lanes import LaneScheduler
from factory_engine.scheduler import TeamScheduler
from factory_engine.store import Store


def prepare(store, batch, home, lanes_path, snapshot_root):
    bid = batch["batch_id"]
    root = Path(snapshot_root).resolve()
    approved = batch["payload"].get("_snapshot_dir")
    if not approved:
        raise ValueError("batch has no immutable approved snapshot")
    root = (store.state_dir / "approved-snapshots").resolve()
    snapshot = Path(approved)
    import hashlib
    expected = batch["payload"].get("_snapshot_manifest", {})
    actual = {}
    for path in snapshot.rglob("*"):
        if path.is_symlink():
            raise ValueError("approved snapshot link forbidden")
        if path.is_file():
            actual[path.relative_to(snapshot).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("approved snapshot changed")
    if snapshot.is_symlink() or root not in snapshot.resolve().parents or not snapshot.is_dir():
        raise ValueError("approved estate snapshot is unavailable")
    folder = Path(home) / "inputs" / bid
    folder.mkdir(parents=True, exist_ok=True)
    task_id = bid[:16]
    db_path = Path(home) / "tasks" / task_id / "factory.db"
    if not db_path.exists():
        argv = ["--home", str(home), "submit", "--snapshot", str(snapshot),
                "--task", task_id, "--no-start", "--json", "--gate", "local"]
        if lanes_path:
            argv += ["--lanes", str(lanes_path)]
        payload = batch["payload"]
        if payload.get("template"):
            target = folder / "template.json"
            target.write_text(json.dumps(payload["template"]), encoding="utf-8")
            argv += ["--template", str(target)]
        if payload.get("cards"):
            cards = [{**c, "task_id": task_id} for c in payload["cards"]]
            target = folder / "cards.json"
            target.write_text(json.dumps(cards), encoding="utf-8")
            argv += ["--cards", str(target)]
        # CLI summaries are local operational data, not service audit payloads.
        rc = cli.main(argv, output_stream=io.StringIO())
        if rc:
            raise ValueError("batch failed schema, packing or gate preflight")
    task = Store(db_path)
    task.set_task(team_id=batch["team_id"], submitted_by=batch["submitted_by"],
                  data_class=batch["data_class"], estate_id=batch["estate_id"], status="running")
    task.db.execute("UPDATE unit SET team_id=?,priority=?", (batch["team_id"], batch["priority"]))
    task.requeue_running()
    return task


async def run_foreground(store, stop_event, home, lanes=None, snapshot_root="/work/estates",
                         starvation_n=20, poll_seconds=1, retention_seconds=43200, before_prune=None):
    cfg = config.load_lanes(Path(lanes) if lanes else None)
    fair = TeamScheduler(store.teams, cfg["lanes"], starvation_n)
    shared = LaneScheduler(cfg, steal=False, teams=store.teams)
    active = {}
    running = {}
    store.recover()
    last_prune = time.monotonic()
    try:
        while not stop_event.is_set():
            if time.monotonic() - last_prune > 60:
                if before_prune:
                    before_prune()
                store.prune(retention_seconds, engine_home=home, exclude=set(active))
                last_prune = time.monotonic()
            with store.lock:
                pending = store.db.execute("SELECT batch_id FROM batches WHERE status='ready' ORDER BY created").fetchall()
            for row in pending:
                bid = row["batch_id"]
                batch = store.claim(bid)
                if not batch:
                    continue
                task = None
                try:
                    task = prepare(store, batch, home, lanes, snapshot_root)
                    engine = Engine(task.path.parent, task, log=lambda message: None, stop=lambda bid=bid: (
                        stop_event.is_set() or store.get(bid)["status"] in ("cancelled", "quarantine")))
                    engine.sched = shared
                    await engine.runner.start({name: repo.root for name, repo in engine.repos.items()})
                    await engine._baselines()
                    active[bid] = ({**batch, "started_monotonic": time.monotonic()}, task, engine)
                except Exception as exc:
                    store.complete(bid, batch["claim_id"], {"error": type(exc).__name__}, "quarantine")
                    if task is not None:
                        task.close()
            ready = []
            for bid, (batch, task, engine) in list(active.items()):
                latest = store.get(bid)
                if time.monotonic() - batch["started_monotonic"] > 900:
                    task.set_task(status="paused", paused_reason="batch runtime cap reached")
                    task.cancel_queued()
                    store.complete(bid, batch["claim_id"], {"error": "runtime cap reached"}, "quarantine")
                if latest["status"] == "cancelled":
                    task.set_task(status="cancelled")
                    task.cancel_queued()
                    continue
                # Renew the batch lease while a foreground supervisor owns it.
                with store.lock:
                    store.db.execute("UPDATE batches SET lease_until=? WHERE batch_id=? AND claim_id=?",
                                     (time.time() + 900, bid, batch["claim_id"]))
                if task.task()["status"] == "paused":
                    continue
                for unit in task.queued(10000):
                    deps = [task.unit(dep) for dep in unit.get("deps") or []]
                    if any(d is None or d["status"] in ("bounced", "cancelled") for d in deps):
                        task.set_unit(unit["unit_id"], status="bounced", bounce_class="DEP_BOUNCED")
                        task.event("unit_bounced", unit["unit_id"], bounce_class="DEP_BOUNCED")
                        continue
                    if any(d["status"] != "accepted" for d in deps):
                        continue
                    ladder = shared.ladder(unit["kind"], unit["difficulty"], int(unit["card"].get("retry", {}).get("max_generations", 3)))
                    rung = ladder[min(int(unit.get("generations") or 0), len(ladder) - 1)]
                    ready.append({**unit, "batch_id": bid, "seq": (batch["created"], unit["seq"], bid),
                                  "team_id": batch["team_id"],
                                  "data_class": batch["data_class"], "lanes": [rung["lane"]]})
            for lane_name, lane in shared.lanes.items():
                reserved = sum(1 for _, reservation in running.values() if reservation["lane"] == lane_name)
                for _ in range(max(0, lane.concurrency - reserved)):
                    selected = fair.choose(ready, lane_name)
                    if selected is None:
                        break
                    ready.remove(selected)
                    batch, task, engine = active[selected["batch_id"]]
                    if not task.claim(selected["unit_id"]):
                        fair.release(lane_name, selected["team_id"])
                        continue
                    task.set_unit(selected["unit_id"], lane=lane_name)
                    future = asyncio.create_task(engine.process(task.unit(selected["unit_id"]), one_generation=True))
                    running[future] = (selected["batch_id"], {"lane": lane_name, "team": selected["team_id"]})
            if running:
                done, _ = await asyncio.wait(running, timeout=poll_seconds, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    bid, reservation = running.pop(future)
                    fair.release(reservation["lane"], reservation["team"])
                    batch, task, engine = active[bid]
                    if not future.exception():
                        engine._check_pause()
                        if task.task()["status"] == "paused":
                            store.complete(bid, batch["claim_id"], {"error": "task paused for review"}, "quarantine")
                    if future.exception():
                        batch, task, engine = active[bid]
                        task.set_task(status="paused", paused_reason="foreground unit failed")
                        store.complete(bid, batch["claim_id"], {"error": type(future.exception()).__name__}, "quarantine")
            else:
                await asyncio.sleep(poll_seconds)
            for bid, (batch, task, engine) in list(active.items()):
                counts = task.counts()
                latest = store.get(bid)
                if latest["status"] in ("cancelled", "quarantine") and not any(v[0] == bid for v in running.values()):
                    await engine.runner.close()
                    task.close()
                    del active[bid]
                    continue
                if not counts["queued"] and not counts["running"]:
                    try:
                        assembled = await bundle.assemble(task, engine.runner, engine.profiles, log=lambda message: None)
                        summary = bundle.build(task, task.path.parent, assembled)
                        task.set_task(status="finished", finished=time.time(), bundle=summary["bundle_tgz"])
                        task.event("finished", verdict=summary["verdict"])
                    except Exception as exc:
                        summary = {"verified": False, "error": type(exc).__name__}
                    counts = task.counts()
                    result = {"counts": counts, "bundle": summary, "units": [
                        {k: u.get(k) for k in ("unit_id", "status", "lane", "output", "bounce_class")}
                        for u in task.units()]}
                    store.complete(bid, batch["claim_id"], result, "done" if summary.get("verified") else "quarantine")
                    await engine.runner.close()
                    task.close()
                    del active[bid]
    finally:
        # Shutdown waits for bounded current generations; it leaves no worker behind.
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        for batch, task, engine in active.values():
            await engine.runner.close()
            task.requeue_running()
            task.close()
            with store.transaction():
                store.db.execute("UPDATE batches SET status='ready',claim_id=NULL WHERE batch_id=? AND status='running'",
                                 (batch["batch_id"],))
