"""`factory` CLI: submit | status | results | bounces | finish  (+ worker, resume, ls, doctor, render, purge).

Submit-and-queue: `submit` validates, expands and packs the cards, stores them for the supervised foreground service, then returns.
An explicit --foreground development run blocks in the current process. Waits are bounded (<= 15 min) and only
happen when asked (`status --wait`, `finish --wait`). Every command takes --json for the
frontier's Bash allowlist / MCP wrapper. Exit codes: 0 ok, 2 usage/validation, 3 not finished
or needs review, 4 runtime error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__, bundle, cards as cards_mod, config, jsbridge, snapshot as snap_mod
from .engine import Engine
from .fsutil import CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, hidden, rmtree
from .gates import make_runner
from .packer import PackError, Repo, chatml, exemplar_text, pack, render_parts
from .schema import SchemaError, validate_or_raise
from .store import Store
from .tokens import Counter

EXIT_OK, EXIT_USAGE, EXIT_PENDING, EXIT_ERROR = 0, 2, 3, 4
HEX16 = set("0123456789abcdef")
_STORES: list[Store] = []          # every store a command opens; main() closes them


def _track(store: Store) -> Store:
    _STORES.append(store)
    return store


class CliError(Exception):
    def __init__(self, msg: str, code: int = EXIT_USAGE):
        super().__init__(msg)
        self.code = code


def out(args, data: dict, human: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, indent=1, default=str), file=getattr(args, "_output_stream", None))
    else:
        print(human, file=getattr(args, "_output_stream", None))


# --------------------------------------------------------------------------- task dirs
def home(args) -> Path:
    return Path(args.home) if getattr(args, "home", None) else config.factory_home()


def task_dir(args, task_id: str) -> Path:
    return home(args) / "tasks" / task_id


def resolve_task(args, ref: str | None) -> str:
    tasks = home(args) / "tasks"
    ids = sorted((p for p in tasks.iterdir() if (p / "factory.db").exists()), key=lambda p: p.stat().st_mtime,
                 reverse=True) if tasks.is_dir() else []
    if not ref or ref in ("last", "@"):
        if not ids:
            raise CliError("no factory tasks yet")
        return ids[0].name
    ref = ref.lower()
    if not set(ref) <= HEX16:
        raise CliError(f"bad task id {ref!r}")
    hits = [p.name for p in ids if p.name.startswith(ref)]
    if len(hits) != 1:
        raise CliError(f"no task matches {ref!r}" if not hits else f"{ref!r} is ambiguous: {', '.join(hits[:5])}")
    return hits[0]


def open_store(args, task_id: str) -> Store:
    p = task_dir(args, task_id) / "factory.db"
    if not p.exists():
        raise CliError(f"task {task_id} not found under {home(args)}")
    return _track(Store(p))


# --------------------------------------------------------------------------- inputs
def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_cards(path: Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    if text.startswith("{") and "\n{" not in text:
        d = json.loads(text)
        return d["cards"] if isinstance(d, dict) and "cards" in d else [d]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _profile_for(repo: str, args, tpl: dict | None, tpl_path: Path | None) -> tuple[dict, Path | None]:
    choice = None
    for spec in args.profile or []:
        if "=" in spec:
            name, _, p = spec.partition("=")
            if name == repo:
                choice = Path(p)
        elif choice is None:
            choice = Path(spec)
    if choice is None and tpl is not None and tpl.get("profile") is not None:
        pv = tpl["profile"]
        if isinstance(pv, dict):
            validate_or_raise("profile.v1", pv, what=f"template {tpl.get('template_id')} profile")
            return pv, (tpl_path.parent if tpl_path else None)
        choice = (tpl_path.parent / pv) if tpl_path else Path(pv)
    if choice is None:
        choice = config.PROFILES_DIR / "jsdoc-cjs.json"
    prof = _load_json(choice)
    validate_or_raise("profile.v1", prof, what=str(choice))
    for holder in (prof, prof.get("jest") or {}):
        ex = holder.get("exemplar") or {}
        if ex.get("file") and not ex.get("text"):
            f = (choice.parent / ex["file"]).resolve()
            if f.is_file() and choice.parent.resolve() in f.parents:
                holder["exemplar"] = {"text": f.read_text(encoding="utf-8")}
    return prof, choice.parent


def _deps(args) -> dict:
    out = {}
    for spec in getattr(args, "deps", None) or []:
        name, _, p = spec.partition("=")
        if not name or not p or not Path(p).is_dir():
            raise CliError(f"--deps {spec}: expected REPO=DIR (an existing node_modules dir)")
        out[name] = str(Path(p).resolve())
    return out


def _tester_cfg(args) -> dict | None:
    if not getattr(args, "tester_workspace", None):
        return None
    testers = []
    for spec in args.tester or []:
        ipc, _, res = spec.partition(",")
        testers.append({"ipc": ipc, "results": res})
    if not testers:
        ws = Path(args.tester_workspace)
        testers = [{"ipc": str(ws / ".ipc"), "results": str(Path(args.tester_testbox or "/testbox") / "results")}]
    return {"workspace": args.tester_workspace, "testers": testers}


# --------------------------------------------------------------------------- submit
def cmd_submit(args) -> int:
    if not args.template and not args.cards:
        raise CliError("give --template FILE (generator sweep) and/or --cards FILE")
    snap = snap_mod.load(Path(args.snapshot), args.repo)
    if not snap.repos:
        raise CliError(f"{args.snapshot}: no repos found (expected MANIFEST.json + <repo>/ dirs, or repo dirs)")
    flags = config.enabled_flags(args.enable)
    lanes = config.load_lanes(Path(args.lanes) if args.lanes else None)
    task_id = args.task or secrets.token_hex(8)
    if not (len(task_id) == 16 and set(task_id) <= HEX16):
        raise CliError("--task must be a 16-char hex id")
    existing = (task_dir(args, task_id) / "factory.db").exists()
    tdir = task_dir(args, task_id) if not args.dry_run else Path(home(args)) / "dry-run" / secrets.token_hex(4)
    tdir.mkdir(parents=True, exist_ok=True)
    store = _track(Store(tdir / "factory.db")) if not args.dry_run else None
    prev = store.task() if store else None
    if prev and Path(prev["snapshot_dir"]).resolve() != snap.dir.resolve():
        raise CliError(f"task {task_id} uses snapshot {prev['snapshot_dir']}, not {snap.dir}")
    tcfg = prev["config"] if prev else {
        "engine": __version__, "snapshot_dir": str(snap.dir), "snapshot_id": snap.snapshot_id, "repos": {},
        "lanes": lanes, "lanes_file": args.lanes, "profiles": {}, "profile_dirs": {}, "flags": sorted(flags),
        "gate": {"runner": args.gate, "tester": _tester_cfg(args), "deps": _deps(args)}, "templates": [],
        "fast_backoff": bool(args.fast_backoff),
    }
    if prev:
        flags |= set(tcfg.get("flags", []))
        lanes = tcfg["lanes"]
    counter = Counter()
    try:
        tcfg["tools"] = jsbridge.check_tools()
    except jsbridge.JsToolError as exc:
        raise CliError(str(exc), EXIT_ERROR) from None
    loaded: dict[str, Repo] = {}

    def repo(name: str) -> Repo:
        if name not in snap.repos:
            raise CliError(f"repo {name} is not in the snapshot ({', '.join(sorted(snap.repos))})")
        if name not in loaded:
            prof = tcfg["profiles"].get(name)
            loaded[name] = Repo.load(name, snap.repos[name].root, cache=tdir / "inventory" / f"{name}.json",
                                     roots=(prof or {}).get("sources"))
        return loaded[name]

    def ensure_repo(name: str, tpl: dict | None = None, tpl_path: Path | None = None) -> None:
        if name not in tcfg["profiles"]:
            prof, pdir = _profile_for(name, args, tpl, tpl_path)
            tcfg["profiles"][name] = prof
            tcfg["profile_dirs"][name] = str(pdir) if pdir else None
        sr = snap.repos.get(name)
        if sr is None:
            raise CliError(f"repo {name} is not in the snapshot ({', '.join(sorted(snap.repos))})")
        tcfg["repos"].setdefault(name, {"root": str(sr.root), "base_commit": sr.base_commit, "tree": sr.tree,
                                        "pins": sr.pins})

    raw: list[dict] = []
    reports = []
    for tp in args.template or []:
        tpath = Path(tp)
        tpl = _load_json(tpath)
        name = (tpl.get("expand") or {}).get("repo") if isinstance(tpl, dict) else None
        if not name:
            raise CliError(f"{tp}: template has no expand.repo")
        ensure_repo(name, tpl, tpath)
        try:
            expanded, report = cards_mod.expand_template(tpl, repo(name), task_id, snap.snapshot_id)
        except cards_mod.CardError as exc:
            raise CliError(f"{tp}: {exc}") from None
        reports.append({"template_id": tpl["template_id"], "kind": tpl["kind"], "repo": name, **report})
        raw += expanded
    for cp in args.cards or []:
        for c in _load_cards(Path(cp)):
            if isinstance(c, dict):
                if c.get("task_id") in (None, "", "0" * 16):
                    c["task_id"] = task_id
                if c.get("repo") in snap.repos:
                    ensure_repo(c["repo"])
            raw.append(c)

    accepted, rejected = [], []
    seen_ids = set()
    # One live unit per (repo, file, symbol) for doc_map and per (repo, test file) for test_gen: a second
    # unit would only burn GPU and then be dropped at assembly (docs) or silently overwrite (tests).
    # Bounced/cancelled units do not count, so a re-decomposed card can be submitted again.
    live = [u for u in store.units(("queued", "running", "accepted"))] if store else []
    claimed_syms = {(u["repo"], u["file"], q) for u in live if u["kind"] == "doc_map" for q in (u["symbols"] or [])}
    claimed_tests = {(u["repo"], ((u["card"] or {}).get("provides") or {}).get("test_file")) for u in live
                     if u["kind"] == "test_gen"}
    for c in raw:
        uid = str(c.get("unit_id", "?")) if isinstance(c, dict) else "?"
        try:
            if not isinstance(c, dict):
                raise cards_mod.CardError(uid, "SCHEMA", "card must be a JSON object")
            if c.get("task_id") != task_id:
                raise cards_mod.CardError(uid, "TASK_MISMATCH", f"card task_id {c.get('task_id')} != {task_id}")
            if c.get("repo") not in snap.repos:
                raise cards_mod.CardError(uid, "REPO_MISSING", f"repo {c.get('repo')!r} is not in the snapshot "
                                          f"({', '.join(sorted(snap.repos))})")
            cards_mod.validate_card(c, flags, repo(c["repo"]) if c.get("repo") in snap.repos else None)
            c = cards_mod.normalize(c)
            if uid in seen_ids or (store and store.has_unit(uid)):
                raise cards_mod.CardError(uid, "DUPLICATE_UNIT", "unit_id already submitted")
            if c["kind"] == "test_gen" and (tcfg["gate"]["runner"] == "local"):
                raise cards_mod.CardError(uid, "GATE_NOT_ISOLATED", "test_gen needs --gate docker or --gate tester")
            r = repo(c["repo"])
            prof = tcfg["profiles"][c["repo"]]
            pdir = Path(tcfg["profile_dirs"][c["repo"]]) if tcfg["profile_dirs"].get(c["repo"]) else None
            if c["kind"] == "doc_map":
                keys = {(c["repo"], c["target"]["file"], q) for q in c["target"]["symbols"]}
                taken = sorted(k[2] for k in keys & claimed_syms)
                if taken:
                    raise cards_mod.CardError(uid, "DUPLICATE_TARGET", f"{', '.join(taken)} in {c['target']['file']} "
                                              "already belong(s) to another live unit of this task")
            else:
                keys = {(c["repo"], c["provides"]["test_file"])}
                if keys & claimed_tests:
                    raise cards_mod.CardError(uid, "DUPLICATE_TEST_FILE", f"{c['provides']['test_file']} is already "
                                              "written by another live unit of this task")
            packed_card, packed = pack(c, r, prof, exemplar_text(c, prof, pdir), counter, lanes)
            seen_ids.add(uid)
            (claimed_syms if c["kind"] == "doc_map" else claimed_tests).update(keys)
            accepted.append(packed_card)
        except cards_mod.CardError as exc:
            rejected.append({"unit_id": exc.unit_id, "code": exc.code, "message": exc.message})
        except PackError as exc:
            rejected.append({"unit_id": uid, "code": exc.code, "message": exc.message, **exc.detail})

    summary = {"task_id": task_id, "snapshot": str(snap.dir), "snapshot_id": snap.snapshot_id,
               "accepted": len(accepted), "rejected": rejected, "templates": reports,
               "counted_by": counter.counted_by,
               "budgets": [{"unit_id": c["unit_id"], "file": c["target"]["file"], "symbols": c["target"]["symbols"],
                            **{k: c["budget"][k] for k in ("prompt_tokens", "max_tokens", "lane_fit")},
                            "dropped": c["budget"]["dropped"]} for c in accepted] if args.dry_run or args.verbose else None}
    if args.dry_run:
        rmtree(tdir)
        out(args, summary, _submit_human(summary, dry=True))
        return EXIT_OK if accepted else EXIT_USAGE
    assert store is not None
    tcfg["templates"] = (tcfg.get("templates") or []) + reports
    if prev is None:
        store.create_task(task_id, snap.snapshot_id, str(snap.dir), tcfg)
    else:
        store.set_task(config=tcfg, status="open" if prev["status"] in ("finished",) else prev["status"])
    seq = store.next_seq()
    with store.tx():
        for c in accepted:
            store.add_unit(c, seq)
            seq += 1
        for r in rejected:
            store.add_rejected(r["unit_id"], r["code"], r["message"])
    store.event("submit", accepted=len(accepted), rejected=len(rejected))
    if accepted and not args.no_start:
        if args.foreground:
            store.close()
            rc = run_worker(args, task_id)
            summary["worker"] = {"mode": "foreground", "exit": rc}
        else:
            summary["worker"] = spawn_worker(args, task_id, store)
    out(args, summary, _submit_human(summary))
    return EXIT_OK if accepted or not rejected else EXIT_USAGE


def _submit_human(s: dict, dry: bool = False) -> str:
    lines = [f"task {s['task_id']}{' (dry run, nothing stored)' if dry else ''}: {s['accepted']} unit(s) accepted, "
             f"{len(s['rejected'])} rejected"]
    for r in s.get("templates") or []:
        lines.append(f"  template {r['template_id']} ({r['kind']} on {r['repo']}): {r['selected']} target(s) -> "
                     f"{r['units']} unit(s)" + (f", {r['capped']} capped" if r.get("capped") else ""))
    for r in s["rejected"][:20]:
        lines.append(f"  rejected {r['unit_id']}: {r['code']}: {r['message'][:160]}")
    for b in (s.get("budgets") or [])[:40]:
        lines.append(f"  {b['unit_id']}: {b['file']} {','.join(b['symbols'])}  prompt {b['prompt_tokens']} + "
                     f"max {b['max_tokens']} -> {','.join(b['lane_fit'])}" + (f"  dropped: {'; '.join(b['dropped'])}" if b['dropped'] else ""))
    w = s.get("worker")
    if w:
        lines.append(f"  worker: {w.get('mode')} " + (f"pid {w['pid']}" if w.get("pid") else f"exit {w.get('exit')}")
                     + (f", log {w['log']}" if w.get("log") else ""))
    if not dry:
        lines.append(f"next: factory status {s['task_id']}   (bounded wait: --wait 600)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- worker
def spawn_worker(args, task_id: str, store: Store) -> dict:
    """Submission persists work for the supervised foreground service."""
    return {"mode": "supervised", "note": "foreground service dispatches queued work"}


def run_worker(args, task_id: str) -> int:
    tdir = task_dir(args, task_id)
    store = Store(tdir / "factory.db")
    if not store.take_lease(config.WORKER_STALE_S):
        print(f"[worker] another worker holds task {task_id}; exiting", file=sys.stderr, flush=True)
        return EXIT_OK
    t = store.task()
    if t["status"] in ("open", "idle"):
        store.set_task(status="running")
    print(f"[worker] task {task_id} pid {os.getpid()} started", file=sys.stderr, flush=True)
    last_stats = [0.0]

    def log(msg: str) -> None:
        print(f"[worker] {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)

    eng = None
    # The lease is refreshed by a thread with its own connection, not by the run loop: gate-runner start-up
    # (a first `docker build`, container starts) and the tsc baselines can take longer than WORKER_STALE_S,
    # and a stale lease lets `finish`/`resume` start a second worker on the same task.
    hb_stop, lease_lost = threading.Event(), threading.Event()

    def hb_loop() -> None:
        db = Store(tdir / "factory.db")
        try:
            while not hb_stop.wait(config.WORKER_HEARTBEAT_S):
                try:
                    if not db.heartbeat():
                        lease_lost.set()
                        return
                except Exception:  # noqa: BLE001 - a locked db: try again next beat
                    pass
        finally:
            db.close()

    hb = threading.Thread(target=hb_loop, name="factory-heartbeat", daemon=True)
    hb.start()

    def heartbeat() -> None:
        if eng and time.monotonic() - last_stats[0] > 5:
            last_stats[0] = time.monotonic()
            try:
                (tdir / "lanes-live.json").write_text(json.dumps({"ts": time.time(), "lanes": eng.sched.stats()}),
                                                      encoding="utf-8")
            except OSError:
                pass

    rc = EXIT_OK
    try:
        # Units appended by `submit --task` while this worker was draining (its lease still looked alive,
        # so submit started no worker) are picked up by another pass instead of being left queued.
        for _ in range(20):
            eng = Engine(tdir, store, log=log, stop=lease_lost.is_set)
            stats = asyncio.run(eng.run(heartbeat=heartbeat, deadline_s=getattr(args, "max_runtime", None)))
            log(f"done: {json.dumps(stats['counts'])}")
            if lease_lost.is_set():
                log("lost the worker lease to another worker; stopped launching")
                break
            if not store.counts()["queued"] or store.task()["status"] not in ("running", "open", "idle"):
                break
    except Exception as exc:  # noqa: BLE001
        log(f"worker error: {type(exc).__name__}: {exc}")
        store.event("worker_error", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        rc = EXIT_ERROR
    finally:
        hb_stop.set()
        hb.join(timeout=10)
        t = store.task()
        if t["status"] == "running" and not lease_lost.is_set():
            store.set_task(status="idle")
        store.release_lease()
        store.close()
    return rc


def cmd_worker(args) -> int:
    return run_worker(args, resolve_task(args, args.task))


def cmd_resume(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    t = store.task()
    if t["status"] == "finished":
        raise CliError(f"task {task_id} is finished")
    store.set_task(status="running", paused_reason=None)
    store.event("resume")
    orphans = store.recover_orphans(config.WORKER_STALE_S)
    if orphans:
        store.event("orphans_requeued", n=orphans)
    w = spawn_worker(args, task_id, store) if store.counts()["queued"] else {"note": "nothing queued"}
    if orphans:
        w = {**w, "requeued_orphans": orphans}
    out(args, {"task_id": task_id, "worker": w}, f"task {task_id} resumed: {w}")
    return EXIT_OK


# --------------------------------------------------------------------------- status / results / bounces
def _status(store: Store) -> dict:
    t = store.task()
    c = store.counts()
    w = store.worker()
    tdir = store.path.parent
    lanes = None
    for name in ("lanes-live.json", "lanes-stats.json"):
        p = tdir / name
        if p.exists():
            try:
                lanes = json.loads(p.read_text(encoding="utf-8")).get("lanes")
                break
            except ValueError:
                pass
    done = c["accepted"] + c["bounced"]
    eta = None
    if w and done and (c["queued"] + c["running"]):
        el = max(1.0, time.time() - (w.get("started") or time.time()))
        eta = round((c["queued"] + c["running"]) * el / done)
    alive = store.worker_alive(config.WORKER_STALE_S)
    return {"task_id": t["task_id"], "status": t["status"], "paused_reason": t.get("paused_reason"),
            "counts": c, "worker": {"alive": alive, **(w or {})},
            "orphaned": 0 if alive else c["running"],
            "lanes": lanes, "eta_s": eta, "bundle": t.get("bundle"), "snapshot_id": t.get("snapshot_id")}


def _status_human(s: dict) -> str:
    c = s["counts"]
    lines = [f"task {s['task_id']}: {s['status']}" + (f" ({s['paused_reason']})" if s.get("paused_reason") else ""),
             f"  units: {c['accepted']} accepted, {c['bounced']} bounced, {c['running']} running, {c['queued']} queued, "
             f"{c['cancelled']} cancelled; {c['rejected']} rejected at submit",
             f"  worker: {'alive pid ' + str(s['worker'].get('pid')) if s['worker']['alive'] else 'not running'}"
             + (f"; eta ~{s['eta_s']}s" if s.get("eta_s") else "")]
    if s.get("orphaned"):
        lines.append(f"  {s['orphaned']} unit(s) were left running by a worker that died: `factory resume {s['task_id']}` "
                     "requeues them and starts a worker")
    for l in s.get("lanes") or []:
        lines.append(f"  lane {l['lane']} ({l.get('model')}): {l['inflight']}/{l['concurrency']} in flight, "
                     f"{l['requests']} req, max {l['max_inflight']}, util {l['slot_utilization']:.0%}"
                     + (f", {l['errors']} errors" if l.get("errors") else ""))
    if s.get("bundle"):
        lines.append(f"  bundle: {s['bundle']}")
    return "\n".join(lines)


def cmd_status(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    wait = min(max(0, int(args.wait or 0)), config.MAX_WAIT_S)
    end = time.monotonic() + wait
    no_worker_since = None
    while True:
        s = _status(store)
        c = s["counts"]
        busy = c["queued"] + c["running"]
        if not s["worker"]["alive"]:          # no live worker (orphaned running units count too)
            no_worker_since = no_worker_since or time.monotonic()
        else:
            no_worker_since = None
        # a just-spawned worker needs a moment to take its lease: give up only when none shows up
        if not wait or time.monotonic() >= end or not busy or s["status"] == "paused" or \
                (no_worker_since and time.monotonic() - no_worker_since > config.WORKER_STARTUP_GRACE_S):
            break
        time.sleep(min(1.0, max(0.1, end - time.monotonic())))
    out(args, s, _status_human(s))
    return EXIT_OK if not (s["counts"]["queued"] + s["counts"]["running"]) else EXIT_PENDING


def cmd_results(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    units = store.units(args.status) if args.status else store.units()
    if args.unit:
        units = [u for u in units if u["unit_id"] in set(args.unit)]
    rows = []
    for u in units:
        row = {"unit_id": u["unit_id"], "status": u["status"], "repo": u["repo"], "file": u["file"],
               "symbols": u["symbols"], "kind": u["kind"], "writer": u.get("writer"), "lane": u.get("lane"),
               "generations": u.get("generations"), "gpu_s": u.get("gpu_s"), "bounce_class": u.get("bounce_class"),
               "attempts": [{"gen": a["gen"], "lane": a["lane"], "stop": a["stop"], "stage": a["stage"], "ok": bool(a["ok"])}
                            for a in store.attempts(u["unit_id"])]}
        if args.detail == "output":
            row["output"] = u.get("output")
        rows.append(row)
    human = "\n".join(f"{r['unit_id']:<20} {r['status']:<9} {r.get('writer') or '-':<10} gens={r['generations'] or 0} "
                      f"{r['file']} {','.join(r['symbols'] or [])}" + (f"  [{r['bounce_class']}]" if r.get("bounce_class") else "")
                      + ("\n" + (r.get("output") or "") if args.detail == "output" and r.get("output") else "")
                      for r in rows) or "no units"
    rej = store.rejected()
    if rej and not args.unit:
        human += "\n" + "\n".join(f"{r['unit_id']:<20} rejected  {r['code']}: {r['message'][:140]}" for r in rej)
    out(args, {"task_id": task_id, "units": rows, "rejected": rej}, human)
    return EXIT_OK


def cmd_bounces(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    bs = [u["bounce"] or {"unit_id": u["unit_id"], "class": u.get("bounce_class")} for u in store.units("bounced")]
    lines = []
    for b in bs:
        lines.append(f"{b.get('unit_id')} [{b.get('class')}] {b.get('file')} {','.join(b.get('symbols') or [])}")
        for a in b.get("attempts", [])[:3]:
            lines.append(f"    gen {a.get('gen')} {a.get('lane')} t={a.get('temperature')}: {a.get('stage')}: "
                         f"{(a.get('message') or '').splitlines()[0][:150] if a.get('message') else ''}")
        for h in b.get("hints", []):
            lines.append(f"    hint: {h}")
    out(args, {"task_id": task_id, "bounces": bs}, "\n".join(lines) or "no bounces")
    return EXIT_OK


# --------------------------------------------------------------------------- finish
def cmd_finish(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    wait = min(max(0, int(args.wait or 0)), config.MAX_WAIT_S)
    end = time.monotonic() + wait
    c = store.counts()
    respawn_at = 0.0
    if c["queued"] + c["running"] and args.partial:
        store.set_task(status="finishing")
        store.event("finishing", partial=True)
    while True:
        # units a dead worker left 'running' would otherwise block finish (and resume) forever
        if store.recover_orphans(config.WORKER_STALE_S):
            store.event("orphans_requeued")
        c = store.counts()
        alive = store.worker_alive(config.WORKER_STALE_S)
        if not (c["queued"] + c["running"]) or time.monotonic() >= end:
            break
        if args.partial and not alive and c["running"] == 0:
            break
        if not alive and not args.partial and c["running"] == 0 and c["queued"] and time.monotonic() > respawn_at:
            # The service observes this task; the CLI never starts a process.
            spawn_worker(args, task_id, store)
            respawn_at = time.monotonic() + config.WORKER_STARTUP_GRACE_S
        time.sleep(1.0)
    c = store.counts()
    if c["running"]:
        raise CliError(f"{c['running']} unit(s) still running; retry `factory finish {task_id} --wait 600` "
                       "(or --partial to bundle what is accepted)", EXIT_PENDING)
    if c["queued"]:
        if not args.partial:
            raise CliError(f"{c['queued']} unit(s) still queued; `factory finish {task_id} --wait 600`, or --partial",
                           EXIT_PENDING)
        n = store.cancel_queued()
        store.event("cancelled_queued", n=n)
    t = store.task()
    cfg = t["config"]
    tdir = task_dir(args, task_id)
    runner_kind = args.gate or cfg.get("gate", {}).get("runner", "local")
    runner = make_runner(runner_kind, parallel=int(cfg["lanes"].get("gate_parallel", 3)), scratch=tdir / "gates",
                         tag=task_id[:8] + "f", tester_cfg=cfg.get("gate", {}).get("tester"),
                         deps=cfg.get("gate", {}).get("deps"))

    async def go():
        await runner.start({n: Path(r["root"]) for n, r in cfg["repos"].items()})
        try:
            return await bundle.assemble(store, runner, cfg["profiles"], log=lambda m: print(m, file=sys.stderr))
        finally:
            await runner.close()
    assembled = asyncio.run(go())
    summary = bundle.build(store, tdir, assembled, out_dir=Path(args.out) if args.out else None)
    if args.register_aa:
        roots = {}
        for spec in args.repo_root or []:
            name, _, p = spec.partition("=")
            roots[name] = Path(p)
        summary["aa"] = bundle.register_aa(summary, store, roots)
    store.set_task(status="finished", finished=time.time(), bundle=summary["bundle_tgz"])
    store.event("finished", verdict=summary["verdict"])
    human = [f"task {task_id}: {summary['verdict']}" + (f" ({'; '.join(summary['reasons'])})" if summary["reasons"] else ""),
             f"  {summary['accepted']} accepted, {summary['bounced']} bounced, {summary['cancelled']} cancelled",
             f"  bundle: {summary['bundle_tgz']}"]
    for r, v in summary["repos"].items():
        human.append(f"  {r}: {v['files']} file(s), {v['patch']}")
    for w in summary.get("warnings") or []:
        human.append(f"  WARNING: {w}")
    if summary.get("aa"):
        human.append(f"  apply: {summary['aa']['apply']}")
    out(args, summary, "\n".join(human))
    return EXIT_OK if summary["verified"] else EXIT_PENDING


# --------------------------------------------------------------------------- misc
def cmd_ls(args) -> int:
    tasks = home(args) / "tasks"
    rows = []
    if tasks.is_dir():
        for p in sorted(tasks.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if (p / "factory.db").exists():
                s = Store(p / "factory.db")
                t = s.task()
                if t:
                    rows.append({"task_id": t["task_id"], "status": t["status"], "counts": s.counts(),
                                 "created": t["created"]})
                s.close()
    out(args, {"tasks": rows}, "\n".join(f"{r['task_id']}  {r['status']:<9} {r['counts']['accepted']} ok / "
                                         f"{r['counts']['bounced']} bounced / {r['counts']['total']} units"
                                         for r in rows) or "no tasks")
    return EXIT_OK


def cmd_doctor(args) -> int:
    from . import client
    report: dict = {"engine": __version__, "python": sys.version.split()[0], "home": str(home(args))}
    try:
        report["node"] = subprocess.run([jsbridge.node_bin(), "--version"], capture_output=True, text=True, **hidden()).stdout.strip()
        report["tools"] = jsbridge.check_tools()
    except jsbridge.JsToolError as exc:
        report["tools_error"] = str(exc)
    report["docker"] = bool(shutil.which("docker"))
    report["tokens"] = Counter().counted_by
    if args.lanes or args.probe:
        lanes = config.load_lanes(Path(args.lanes) if args.lanes else None)
        report["lanes"] = {n: client.probe(l) for n, l in lanes["lanes"].items()} if args.probe else \
            config.public_lanes(lanes)["lanes"]
    out(args, report, json.dumps(report, indent=1))
    return EXIT_OK if "tools_error" not in report else EXIT_ERROR


def cmd_render(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    u = store.unit(args.unit)
    if not u:
        raise CliError(f"no unit {args.unit}")
    cfg = store.task()["config"]
    r = cfg["repos"][u["repo"]]
    repo = Repo.load(u["repo"], Path(r["root"]), cache=task_dir(args, task_id) / "inventory" / f"{u['repo']}.json")
    prof = cfg["profiles"][u["repo"]]
    pdir = cfg.get("profile_dirs", {}).get(u["repo"])
    ex = None if "exemplar dropped" in u["card"]["budget"].get("dropped", []) else \
        exemplar_text(u["card"], prof, Path(pdir) if pdir else None)
    system, user = render_parts(u["card"], repo, prof, ex)
    print(chatml(system, user, config.OPEN + "\n"))
    return EXIT_OK


def cmd_purge(args) -> int:
    task_id = resolve_task(args, args.task)
    store = open_store(args, task_id)
    if store.worker_alive(config.WORKER_STALE_S):
        raise CliError("a worker is running for this task; finish or wait first")
    store.close()
    rmtree(task_dir(args, task_id))
    out(args, {"task_id": task_id, "purged": True}, f"task {task_id} deleted")
    return EXIT_OK


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory", description="agent-array factory engine: generator sweeps on the "
                                "local GPU lanes, gated, bundled for `aa get --apply`. Never calls a frontier model.")
    p.add_argument("--version", action="version", version=f"factory-engine {__version__}")
    p.add_argument("--home", help="state dir (default FACTORY_HOME or the per-user state dir)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    s = sub.add_parser("submit", help="expand/validate/pack cards, store them, queue for the supervised foreground service")
    s.add_argument("--snapshot", required=True, help="snapshot dir (aa snapshot layout) or a repo copy")
    s.add_argument("--repo", help="repo name when --snapshot is a single repo dir")
    s.add_argument("--template", action="append", metavar="FILE", help="generator template (template.v1), repeatable")
    s.add_argument("--cards", action="append", metavar="FILE", help="cards (JSON array, JSONL or {cards:[...]}), repeatable")
    s.add_argument("--task", help="add to this task (16 hex) instead of creating one")
    s.add_argument("--lanes", help="lanes/routing overlay (lanes.v1)")
    s.add_argument("--profile", action="append", metavar="[REPO=]FILE", help="repo profile (profile.v1)")
    s.add_argument("--enable", action="append", default=[], metavar="FLAG", help="feature flag, e.g. test_gen")
    s.add_argument("--gate", choices=["local", "docker", "tester"], default="local",
                   help="gate runner: local (host node; doc_map only), docker, tester (sidecar IPC)")
    s.add_argument("--deps", action="append", metavar="REPO=DIR",
                   help="node_modules for a repo whose snapshot copy has none (a deps cache built from the real lockfile)")
    s.add_argument("--tester-workspace", help="tester runner: the workspace the testers mount (/workspace)")
    s.add_argument("--tester-testbox", help="tester runner (v1): the tester's /testbox")
    s.add_argument("--tester", action="append", metavar="IPC,RESULTS", help="tester runner (v2): one per tester")
    s.add_argument("--dry-run", action="store_true", help="expand + pack + print budgets; store nothing")
    s.add_argument("--no-start", action="store_true", help="store the units but do not start a worker")
    s.add_argument("--foreground", action="store_true", help="run the worker in this process (blocks)")
    s.add_argument("--fast-backoff", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--max-runtime", type=float, help=argparse.SUPPRESS)
    s.add_argument("-v", "--verbose", action="store_true", help="print per-unit budgets")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_submit)

    st = sub.add_parser("status", help="counts, lanes, worker, ETA (bounded --wait)")
    st.add_argument("task", nargs="?")
    st.add_argument("--wait", type=int, default=0, help=f"block up to N s (max {config.MAX_WAIT_S}) until the queue drains")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    r = sub.add_parser("results", help="per-unit results")
    r.add_argument("task", nargs="?")
    r.add_argument("--status", choices=["queued", "running", "accepted", "bounced", "cancelled"])
    r.add_argument("--unit", action="append")
    r.add_argument("--detail", choices=["summary", "output"], default="summary")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_results)

    b = sub.add_parser("bounces", help="bounce dossiers for the frontier (best candidate, attempts, hints)")
    b.add_argument("task", nargs="?")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_bounces)

    f = sub.add_parser("finish", help="assemble + integration-check + write the bundle")
    f.add_argument("task", nargs="?")
    f.add_argument("--wait", type=int, default=0, help=f"wait up to N s (max {config.MAX_WAIT_S}) for the queue")
    f.add_argument("--partial", action="store_true", help="stop launching, cancel what is queued, bundle the rest")
    f.add_argument("--out", help="write bundle/ + bundle.tgz here instead of the task dir")
    f.add_argument("--gate", choices=["local", "docker", "tester"], help="runner for assembly checks (default: the task's)")
    f.add_argument("--register-aa", action="store_true", help="write an aa ledger record so `aa get <task> --apply` works")
    f.add_argument("--repo-root", action="append", metavar="NAME=PATH", help="your checkout of each repo (with --register-aa)")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_finish)

    w = sub.add_parser("worker", help="(internal) run the task's worker in this process")
    w.add_argument("task")
    w.add_argument("--max-runtime", type=float)
    w.set_defaults(func=cmd_worker)

    rs = sub.add_parser("resume", help="clear a pause and restart the worker")
    rs.add_argument("task", nargs="?")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_resume)

    ls = sub.add_parser("ls", help="list tasks")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_ls)

    d = sub.add_parser("doctor", help="check node/tools/docker; --probe the lanes")
    d.add_argument("--lanes")
    d.add_argument("--probe", action="store_true")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    rd = sub.add_parser("render", help="print a unit's exact prompt")
    rd.add_argument("task")
    rd.add_argument("unit")
    rd.set_defaults(func=cmd_render)

    pg = sub.add_parser("purge", help="delete a task dir (store, inventory, bundle)")
    pg.add_argument("task")
    pg.add_argument("--json", action="store_true")
    pg.set_defaults(func=cmd_purge)
    return p


def main(argv: list[str] | None = None, *, output_stream=None) -> int:
    args = build_parser().parse_args(argv)
    args._output_stream = output_stream
    try:
        return _run(args)
    finally:
        while _STORES:
            try:
                _STORES.pop().close()
            except Exception:  # noqa: BLE001
                pass


def _run(args) -> int:
    try:
        return int(args.func(args) or 0)
    except CliError as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return exc.code
    except SchemaError as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        return 130
