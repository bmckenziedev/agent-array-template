"""GPU lane scheduler: per-slot admission and bounded concurrency (GPU v2: 1/3).

Routing order is configured, not evidence of model quality.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from . import config


class NoLane(Exception):
    """No lane of this rung (or its steal targets) fits the prompt."""


@dataclass
class Lane:
    name: str
    cfg: dict
    sem: asyncio.Semaphore
    concurrency: int
    inflight: int = 0
    max_inflight: int = 0
    requests: int = 0
    stolen: int = 0
    errors: int = 0
    busy_s: float = 0.0
    down_until: float = 0.0
    created: float = field(default_factory=time.monotonic)

    @property
    def tier(self) -> str:
        return str(self.cfg.get("tier") or self.name)

    @property
    def writer(self) -> str:
        return f"local-{self.tier}"

    def saturated(self) -> bool:
        return self.inflight >= self.concurrency

    def down(self) -> bool:
        return time.monotonic() < self.down_until

    def fits(self, prompt_tokens: int, max_tokens: int) -> bool:
        return (prompt_tokens <= int(self.cfg["prompt_cap"])
                and prompt_tokens + max_tokens <= int(self.cfg["ctx"]) - config.CTX_MARGIN)

    def stats(self) -> dict:
        up = max(1e-6, time.monotonic() - self.created)
        return {"lane": self.name, "tier": self.tier, "concurrency": self.concurrency, "inflight": self.inflight,
                "max_inflight": self.max_inflight, "requests": self.requests, "stolen": self.stolen,
                "errors": self.errors, "busy_s": round(self.busy_s, 2),
                "slot_utilization": round(self.busy_s / (up * self.concurrency), 3), "down": self.down(),
                "model": self.cfg.get("endpoint", {}).get("model")}


class LaneScheduler:
    def __init__(self, lanes_cfg: dict, steal: bool | None = None, teams=None):
        self.teams = teams or {}
        self.team_sems = {}
        self.cfg = lanes_cfg
        self.steal = (lanes_cfg.get("steal", True) if steal is None else steal) and not bool(teams)
        self.lanes: dict[str, Lane] = {}
        for name, lc in lanes_cfg["lanes"].items():
            n = int(lc.get("concurrency", 1))
            self.lanes[name] = Lane(name, lc, asyncio.Semaphore(n), n)

    def ladder(self, kind: str, difficulty: str, max_generations: int) -> list[dict]:
        return list(self.cfg["routing"][kind][difficulty])[:max(1, min(max_generations, config.MAX_GENERATIONS))]

    def total_slots(self) -> int:
        return sum(l.concurrency for l in self.lanes.values())

    def candidates(self, rung: dict, prompt_tokens: int, max_tokens: int) -> list[Lane]:
        names = [rung["lane"], *rung.get("steal_to", [])]
        return [self.lanes[n] for n in names if n in self.lanes and self.lanes[n].fits(prompt_tokens, max_tokens)]

    async def _acquire(self, rung: dict, prompt_tokens: int, max_tokens: int) -> tuple[Lane, bool]:
        cands = self.candidates(rung, prompt_tokens, max_tokens)
        if not cands:
            raise NoLane(f"no lane fits prompt {prompt_tokens} + max_tokens {max_tokens} for rung {rung['lane']}")
        up = [c for c in cands if not c.down()] or cands
        primary = up[0]
        if self.steal and (primary.saturated() or primary.down()):
            for alt in up[1:]:
                if not alt.saturated() and not alt.down():
                    await alt.sem.acquire()
                    return alt, True
        if primary.down():
            await asyncio.sleep(max(0.0, min(primary.down_until - time.monotonic(), 30.0)))
        await primary.sem.acquire()
        return primary, primary.name != rung["lane"]

    @asynccontextmanager
    async def slot(self, rung: dict, prompt_tokens: int, max_tokens: int, *, team_id=None, data_class=None):
        restricted = data_class == "restricted"
        allowed = [name for name in [rung["lane"], *rung.get("steal_to", [])]
                   if name in self.lanes and (not restricted or self.lanes[name].cfg.get("local") is True)]
        if not allowed:
            raise NoLane("data class forbids available lanes")
        safe_rung = {**rung, "lane": allowed[0], "steal_to": allowed[1:]}
        # Team semaphore precedes lane acquisition so an over-cap team cannot occupy a lane.
        team_sem = None
        if team_id is not None:
            team = self.teams.get(team_id)
            if team is None:
                raise NoLane("unknown team identity")
            cap = int(team.get("factory", {}).get("max_inflight_per_lane", 1))
            key = (safe_rung["lane"], team_id)
            team_sem = self.team_sems.setdefault(key, asyncio.Semaphore(cap))
            await team_sem.acquire()
        try:
            lane, stolen = await self._acquire(safe_rung, prompt_tokens, max_tokens)
        except BaseException:
            if team_sem:
                team_sem.release()
            raise
        lane.inflight += 1
        lane.max_inflight = max(lane.max_inflight, lane.inflight)
        lane.requests += 1
        if stolen:
            lane.stolen += 1
        t0 = time.monotonic()
        try:
            yield lane, stolen
        finally:
            lane.busy_s += time.monotonic() - t0
            lane.inflight -= 1
            lane.sem.release()
            if team_sem:
                team_sem.release()

    def mark_error(self, lane: Lane, cooldown_s: float = 15.0) -> None:
        lane.errors += 1
        lane.down_until = time.monotonic() + cooldown_s

    def mark_ok(self, lane: Lane) -> None:
        lane.down_until = 0.0

    def stats(self) -> list[dict]:
        return [l.stats() for l in self.lanes.values()]
