"""The factory run loop (one worker process per task).

Per unit: render (stable prefix first) -> lane slot (semaphore) -> one generation -> G0 envelope ->
gate runner (testers semaphore) -> accept, or feed the gate message back on the next rung.
At most 3 generations per unit (the routing ladder), plus a GPU-seconds budget. Environment
failures (endpoint down, gate environment, target drift) never count as model failures: endpoint
errors are retried and then requeue the unit with a delay; gate-environment and target failures
bounce at once with the right class, so a retry is never wasted on something it cannot fix.

The engine never calls a frontier model and never starts a frontier turn (rules R1-R5). Bounces
wait for the user's next session (`factory bounces`).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import zlib
from pathlib import Path

from . import client, config
from .gates import GateRefused, GateRunner, make_runner
from .lanes import LaneScheduler, NoLane
from .packer import Repo, prompt_count, exemplar_text, feedback_block, render_parts
from .store import Store
from .tokens import Counter

ENV_STAGES = {"gate_env": "GATE_ENV", "gate_error": "GATE_ENV", "target": "TARGET_DRIFT", "request": "GATE_ENV",
              "test_env": "TEST_ENV"}
STAGE_ORDER = {
    "doc_map": ["envelope", "parse", "scope", "hygiene", "splice", "doc_coverage", "type_strength", "tsc", "pass"],
    "test_gen": ["envelope", "parse", "hygiene", "free_ident", "scope", "assert_strength", "jest", "min_tests",
                 "mutants", "pass"],
}
MAX_INFRA_REQUEUES = 5
INFRA_DELAY_S = 20.0
GATE_TIMEOUT_S = {"doc_map": 300, "test_gen": 900}


class InfraFailed(Exception):
    pass


def seed_for(unit_id: str, gen: int) -> int:
    return (config.BASE_SEED + zlib.crc32(unit_id.encode()) + gen * 7919) & 0x7FFFFFFF


def _norm(code: str) -> str:
    return re.sub(r"\s+", " ", code).strip()


def _trim_gate(res: dict) -> dict:
    out = {k: v for k, v in res.items() if k not in ("spliced",)}
    if isinstance(out.get("detail"), list):
        out["detail"] = out["detail"][:20]
    if isinstance(out.get("message"), str):
        out["message"] = out["message"][:4000]
    return out


def _hints(kind: str, history: list[dict]) -> list[str]:
    stages = [h["stage"] for h in history]
    msgs = " ".join(h.get("message") or "" for h in history)
    hints = []
    if stages and all(s == "envelope" for s in stages):
        hints.append("format: the model never produced the <<<CODE ... CODE>>> envelope; check the lane template")
    if "tsc" in stages:
        hints.append("needs_context: tsc rejected the documented types; add call sites or type definitions to the context")
        if re.search(r"TS2(345|322|339)", msgs):
            hints.append("types disagree with callers: a concrete type the callers do not satisfy")
    if "doc_coverage" in stages:
        hints.append("coverage: @param names/order or @returns did not match the code")
    if "type_strength" in stages:
        hints.append("weak_types: the model fell back to any-like types; give it the type definitions it needs")
    if kind == "test_gen" and re.search(r"Cannot find module|ECONNREFUSED|PG_|DATABASE_URL", msgs):
        hints.append("test_env: import-time side effects; extend the repo test profile (dummy env, mocks)")
    if "mutants" in stages:
        hints.append("weak_tests: tests pass but miss seeded faults; ask for value assertions on the branches")
    return hints


class Engine:
    def __init__(self, task_dir: Path, store: Store, *, runner: GateRunner | None = None, log=None,
                 counter: Counter | None = None, stop=None):
        self.task_dir = Path(task_dir)
        self.store = store
        self.stop = stop or (lambda: False)      # e.g. the worker lost its lease: launch nothing more
        self.log = log or (lambda m: None)
        t = store.task()
        if t is None:
            raise RuntimeError("task not initialised")
        self.task_id = t["task_id"]
        self.cfg = t["config"]
        self.lanes_cfg = self.cfg["lanes"]
        self.sched = LaneScheduler(self.lanes_cfg)
        self.counter = counter or Counter()
        self.profiles: dict = self.cfg["profiles"]
        self.profile_dirs = {k: (Path(v) if v else None) for k, v in self.cfg.get("profile_dirs", {}).items()}
        self.repos: dict[str, Repo] = {}
        for name, r in self.cfg["repos"].items():
            self.repos[name] = Repo.load(name, Path(r["root"]), cache=self.task_dir / "inventory" / f"{name}.json",
                                         roots=self.profiles.get(name, {}).get("sources"))
        g = self.cfg.get("gate", {})
        self.runner = runner or make_runner(g.get("runner", "local"), parallel=int(self.lanes_cfg.get("gate_parallel", 3)),
                                            scratch=self.task_dir / "gates", tag=self.task_id[:8],
                                            tester_cfg=g.get("tester"), deps=g.get("deps"))
        n = int(self.lanes_cfg.get("max_inflight_units") or 0)
        self.max_inflight = n or (2 * self.sched.total_slots() + self.runner.parallel)
        self.infra_retries = int(self.lanes_cfg.get("infra_retries", 3))
        self._renders: dict[str, tuple[str, str]] = {}
        self._consecutive_infra = 0
        self.started = time.time()

    # ------------------------------------------------------------------ rendering
    def _exemplar(self, card: dict) -> str | None:
        if "exemplar dropped" in (card.get("budget") or {}).get("dropped", []):
            return None
        return exemplar_text(card, self.profiles.get(card["repo"], {}), self.profile_dirs.get(card["repo"]))

    def render(self, card: dict, feedback: str | None, cap: int) -> tuple[str, str, int]:
        repo = self.repos[card["repo"]]
        profile = self.profiles.get(card["repo"], {})
        ex = self._exemplar(card)
        if feedback is None:
            key = card["unit_id"]
            if key not in self._renders:
                self._renders[key] = render_parts(card, repo, profile, ex)
            system, user = self._renders[key]
            return system, user, prompt_count(self.counter, self.lanes_cfg["lanes"], system, user)
        # design: a retry appends the gate message (<= 600 tokens); over the cap, drop the exemplar first
        for ex_try, fb in ((ex, feedback), (None, feedback), (None, feedback[:800]), (None, None)):
            system, user = render_parts(card, repo, profile, ex_try, None, fb)
            n = prompt_count(self.counter, self.lanes_cfg["lanes"], system, user)
            if n <= cap:
                return system, user, n
        return system, user, n

    # ------------------------------------------------------------------ generation
    async def _generate(self, lane, system: str, user: str, max_tokens: int, rung: dict, seed: int):
        last = None
        for i in range(self.infra_retries + 1):
            if self.stop():
                raise InfraFailed("generation cancelled by service")
            started = time.time()
            try:
                g = await asyncio.to_thread(client.generate, lane.cfg, system, user, max_tokens, rung, seed)
                self.sched.mark_ok(lane)
                self._consecutive_infra = 0
                return g
            except client.InfraError as exc:
                last = str(exc)
            except Exception as exc:  # noqa: BLE001 - e.g. HTTP 400 from a server: still not a model failure
                last = f"{type(exc).__name__}: {str(exc)[:300]}"
            finally:
                self.store.event("generation_occupancy", lane=lane.name,
                                 start=started, end=time.time())
            self.sched.mark_error(lane)
            self._consecutive_infra += 1
            if self.stop():
                raise InfraFailed("generation cancelled by service")
            if i < self.infra_retries:
                await asyncio.sleep(min(2 ** i, 10) * (0.05 if self.cfg.get("fast_backoff") else 1.0))
        raise InfraFailed(f"lane {lane.name}: {last}")

    # ------------------------------------------------------------------ one unit
    async def process(self, unit: dict, *, one_generation: bool = False) -> None:
        card = unit["card"]
        uid = card["unit_id"]
        kind = card["kind"]
        repo_name = card["repo"]
        profile = self.profiles.get(repo_name, {})
        mt = int(card["budget"]["max_tokens"])
        ladder = self.sched.ladder(kind, card["difficulty"], int(card["retry"].get("max_generations", 3)))
        gpu_budget = float(card["retry"].get("gpu_seconds", 180))
        gpu_s = float(unit.get("gpu_s") or 0.0)
        feedback = None
        history: list[dict] = []
        seen: dict[str, dict] = {}
        gens = int(unit.get("generations") or 0)
        if gens:
            # resumed (worker restart or endpoint requeue): restore what the spent generations taught
            for a in self.store.attempts(uid):
                if a["gen"] > gens or a["stage"] is None:
                    continue
                history.append({"gen": a["gen"], "lane": a["lane"], "model": a["model"], "temperature": a["temperature"],
                                "stop": a["stop"], "stage": a["stage"], "message": a["message"], "output": a["output"]})
            if history and history[-1]["stage"] != "pass":
                feedback = feedback_block(history[-1]["stage"], history[-1]["message"] or "", kind, self.counter)
        self.store.event("unit_start", uid)
        for gen, rung in enumerate(ladder, 1):
            if gen <= gens:
                continue      # resumed after a worker restart: those generations were already spent
            cands = self.sched.candidates(rung, 1, mt)
            cap = max((int(l.cfg["prompt_cap"]) for l in cands), default=0)
            system, user, pt = self.render(card, feedback if rung.get("feedback") else None, cap)
            seed = seed_for(uid, gen)
            try:
                async with self.sched.slot(rung, pt, mt, team_id=self.store.task().get("team_id"), data_class=self.store.task().get("data_class")) as (lane, stolen):
                    g = await self._generate(lane, system, user, mt, rung, seed)
            except NoLane as exc:
                history.append({"gen": gen, "lane": rung["lane"], "stage": "no_lane", "message": str(exc)})
                if one_generation:
                    self._bounce(unit, "TOO_LARGE", history, gpu_s)
                    return
                continue
            except InfraFailed as exc:
                self._requeue_infra(unit, str(exc))
                return
            gpu_s += g.latency_s
            gens = gen
            env = g.envelope
            aid = self.store.add_attempt(
                uid, gen=gen, lane=lane.name, model=lane.cfg["endpoint"].get("model"), writer=lane.writer,
                stolen=int(stolen), temperature=float(rung.get("temperature", 0.2)), seed=seed,
                prompt_tokens=g.prompt_tokens or pt, completion_tokens=g.completion_tokens,
                latency_s=round(g.latency_s, 3), stop=g.stop, envelope_ok=int(env.ok),
                output=(env.code if env.ok else g.text)[:20000])
            self.store.set_unit(uid, generations=gens, gpu_s=round(gpu_s, 3))
            if self.stop():
                self.store.update_attempt(aid, stage="cancelled", ok=0, message="cancelled by service")
                self.store.set_unit(uid, status="cancelled", lane=lane.name)
                self.store.event("unit_cancelled", uid, lane=lane.name)
                return
            if not env.ok:
                history.append({"gen": gen, "lane": lane.name, "model": lane.cfg["endpoint"].get("model"),
                                "temperature": rung.get("temperature"), "stop": g.stop, "stage": "envelope",
                                "message": env.reason, "output": g.text[:4000]})
                self.store.update_attempt(aid, stage="envelope", ok=0, message=env.reason)
                feedback = feedback_block("envelope", f"{env.reason}. Start with the JSON/code directly after "
                                          "<<<CODE and end with a line containing only CODE>>>.", kind, self.counter)
            else:
                key = _norm(env.code)
                if key in seen:
                    res = dict(seen[key], duplicate=True)
                else:
                    try:
                        res = await self.runner.run(repo_name, {"kind": kind, "unit": card, "code": env.code,
                                                                "profile": profile}, GATE_TIMEOUT_S.get(kind, 600))
                    except GateRefused as exc:
                        res = {"ok": False, "stage": "gate_env", "message": str(exc)}
                    seen[key] = res
                stage = res.get("stage") or ("pass" if res.get("ok") else "gate_error")
                msg = (res.get("message") or "")[:4000]
                self.store.update_attempt(aid, stage=stage, ok=int(bool(res.get("ok"))), message=msg,
                                          gate_ms=int((res.get("timings") or {}).get("total_ms") or 0))
                history.append({"gen": gen, "lane": lane.name, "model": lane.cfg["endpoint"].get("model"),
                                "temperature": rung.get("temperature"), "stop": g.stop, "stage": stage,
                                "message": msg, "output": env.code[:6000]})
                if res.get("ok"):
                    self.store.set_unit(uid, status="accepted", output=env.code, gate=_trim_gate(res), writer=lane.writer,
                                        lane=lane.name, model=lane.cfg["endpoint"].get("model"))
                    self.store.event("unit_accepted", uid, gen=gen, lane=lane.name)
                    self.log(f"{uid}: accepted (gen {gen}, {lane.name})")
                    return
                if stage in ENV_STAGES:
                    self._bounce(unit, ENV_STAGES[stage], history, gpu_s)
                    return
                feedback = feedback_block(stage, msg, kind, self.counter)
            if gpu_s >= gpu_budget:
                history.append({"gen": gen, "stage": "gpu_budget", "message": f"{gpu_s:.0f}s >= {gpu_budget:.0f}s"})
                break
            if one_generation and gen < len(ladder):
                self.store.set_unit(uid, status="queued", generations=gens, gpu_s=round(gpu_s, 3))
                self.store.event("unit_retry", uid, generation=gen, next_lane=ladder[gen]["lane"])
                return
        last = next((h["stage"] for h in reversed(history) if h.get("stage") not in ("gpu_budget",)), "no_lane")
        cls = {"no_lane": "TOO_LARGE", "infra": "INFRA"}.get(last, last.upper())
        self._bounce(unit, cls, history, gpu_s)

    def _best(self, kind: str, history: list[dict]) -> dict | None:
        order = STAGE_ORDER.get(kind, [])
        cands = [h for h in history if h.get("output") and h.get("stage") in order]
        if not cands:
            return None
        return max(cands, key=lambda h: (order.index(h["stage"]), h["gen"]))

    def _bounce(self, unit: dict, cls: str, history: list[dict], gpu_s: float) -> None:
        card = unit["card"]
        best = self._best(card["kind"], history)
        dossier = {
            "class": cls, "unit_id": card["unit_id"], "repo": card["repo"], "file": card["target"]["file"],
            "symbols": card["target"]["symbols"], "kind": card["kind"], "difficulty": card["difficulty"],
            "attempts": [{k: h.get(k) for k in ("gen", "lane", "model", "temperature", "stop", "stage", "message")}
                         for h in history],
            "best_candidate": ({"gen": best["gen"], "stage": best["stage"], "output": best["output"]} if best else None),
            "first_failure": next((h.get("message") for h in history if h.get("message")), None),
            "hints": _hints(card["kind"], history),
            "gpu_s": round(gpu_s, 2),
        }
        self.store.set_unit(card["unit_id"], status="bounced", bounce_class=cls, bounce=dossier, gpu_s=round(gpu_s, 3))
        self.store.event("unit_bounced", card["unit_id"], cls=cls)
        self.log(f"{card['unit_id']}: bounced ({cls})")

    def _requeue_infra(self, unit: dict, why: str) -> None:
        uid = unit["unit_id"]
        cur = self.store.unit(uid) or unit
        n = int(cur.get("infra_fails") or 0) + 1
        if n > MAX_INFRA_REQUEUES:
            self._bounce(cur, "INFRA", [{"gen": cur.get("generations"), "stage": "infra", "message": why}],
                         float(cur.get("gpu_s") or 0))
            return
        delay = INFRA_DELAY_S * (0.01 if self.cfg.get("fast_backoff") else 1.0) * n
        self.store.set_unit(uid, status="queued", infra_fails=n, not_before=time.time() + delay)
        self.store.event("unit_requeued_infra", uid, why=why[:300], n=n)
        self.log(f"{uid}: endpoint failure, requeued ({n}/{MAX_INFRA_REQUEUES}): {why[:120]}")

    # ------------------------------------------------------------------ the loop
    def _check_pause(self) -> None:
        c = self.store.counts()
        done = c["accepted"] + c["bounced"]
        frac = float(self.lanes_cfg.get("pause_bounce_fraction", 0.3))
        min_done = int(self.lanes_cfg.get("pause_min_done", 10))
        t = self.store.task()
        if t["status"] in ("paused", "finishing", "finished", "cancelled"):
            return
        if done >= min_done and c["bounced"] / max(1, done) > frac:
            self.store.set_task(status="paused", paused_reason=f"{c['bounced']}/{done} units bounced (> {frac:.0%}); "
                                "re-decompose, then `factory resume`")
            self.store.event("task_paused", reason="bounce_fraction")
        elif self._consecutive_infra >= 20:
            self.store.set_task(status="paused", paused_reason="model lanes unreachable (20 consecutive endpoint "
                                "failures); fix the lanes, then `factory resume`")
            self.store.event("task_paused", reason="lanes_down")

    async def _baselines(self) -> None:
        for name in sorted({u["repo"] for u in self.store.units("queued") if u["kind"] == "doc_map"}):
            res = await self.runner.run(name, {"kind": "tsc_baseline", "profile": self.profiles.get(name, {})}, 600)
            if res.get("ok"):
                self.store.event("tsc_baseline", repo=name, count=res.get("count"), ms=res.get("ms"))
                continue
            msg = f"tsc baseline failed for {name}: {res.get('message')}"
            self.log(msg)
            for u in self.store.units("queued", repo=name):
                if u["kind"] == "doc_map":
                    self._bounce(u, "GATE_ENV", [{"gen": 0, "stage": "gate_env", "message": msg}], 0.0)

    async def run(self, *, heartbeat=None, deadline_s: float | None = None) -> dict:
        self.store.requeue_running()
        await self.runner.start({n: r.root for n, r in self.repos.items()})
        running: dict[asyncio.Task, str] = {}
        try:
            await self._baselines()
            t_end = time.monotonic() + deadline_s if deadline_s else None
            while True:
                status = self.store.task()["status"]
                stop_launch = status in ("paused", "finishing", "finished", "cancelled") or self.stop()
                if not stop_launch and len(running) < self.max_inflight:
                    for u in self.store.queued(self.max_inflight * 2):
                        if len(running) >= self.max_inflight:
                            break
                        deps = u.get("deps") or []
                        if deps:
                            states = {d: (self.store.unit(d) or {}).get("status") for d in deps}
                            if any(s in ("bounced", "cancelled", None) for s in states.values()):
                                self._bounce(u, "DEP_BOUNCED", [{"gen": 0, "stage": "deps", "message": json.dumps(states)}], 0.0)
                                continue
                            if any(s != "accepted" for s in states.values()):
                                continue
                        if not self.store.claim(u["unit_id"]):
                            continue
                        fresh = self.store.unit(u["unit_id"])
                        running[asyncio.create_task(self.process(fresh))] = u["unit_id"]
                if not running:
                    if stop_launch:
                        break
                    queued = self.store.units("queued")
                    if not queued:
                        break           # queue drained
                    now = time.time()
                    later = [float(u.get("not_before") or 0) for u in queued if float(u.get("not_before") or 0) > now]
                    if later:           # endpoint-failure requeues waiting out their delay
                        await asyncio.sleep(min(max(0.05, min(later) - now), config.WORKER_HEARTBEAT_S))
                        if heartbeat:
                            heartbeat()
                        continue
                    for u in queued:    # eligible but never launchable: blocked on deps that cannot resolve
                        self._bounce(u, "DEP_BOUNCED", [{"gen": 0, "stage": "deps", "message": "unresolvable deps"}], 0.0)
                    break
                done, _ = await asyncio.wait(list(running), timeout=config.WORKER_HEARTBEAT_S,
                                             return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    uid = running.pop(t)
                    exc = t.exception()
                    if exc is not None:
                        self.log(f"{uid}: engine error {type(exc).__name__}: {exc}")
                        u = self.store.unit(uid)
                        self._bounce(u, "ENGINE_ERROR", [{"gen": u.get("generations"), "stage": "engine",
                                                          "message": f"{type(exc).__name__}: {str(exc)[:500]}"}],
                                     float(u.get("gpu_s") or 0))
                if done:
                    self._check_pause()
                if heartbeat:
                    heartbeat()
                if t_end and time.monotonic() > t_end:
                    self.store.set_task(status="paused", paused_reason="worker deadline reached")
        finally:
            for t in running:
                t.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
            await self.runner.close()
        stats = {"lanes": self.sched.stats(), "gate": {**self.runner.describe(), "calls": self.runner.calls,
                                                       "busy_s": round(self.runner.busy_s, 2)},
                 "counts": self.store.counts(), "wall_s": round(time.time() - self.started, 2)}
        self.store.event("worker_done", **stats)
        (self.task_dir / "lanes-stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
        return stats
