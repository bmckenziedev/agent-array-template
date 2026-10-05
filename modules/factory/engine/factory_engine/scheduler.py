"""Per-lane weighted deficit admission across teams.

Only eligible work accrues credit. A guard overrides credit after N-1 dispatches;
N must be at least the number of simultaneously ready teams. Unit cost is one.
"""
from collections import defaultdict
import json
from pathlib import Path
import threading


class TeamScheduler:
    def __init__(self, teams, lanes, starvation_n=20):
        if isinstance(teams, (str, Path)):
            teams = json.loads(Path(teams).read_text())
        if isinstance(teams, dict):
            teams = teams.get("teams", teams)
        self.teams = {t["id"]: t for t in teams} if isinstance(teams, list) else teams
        self.lanes = lanes
        self.starvation_n = starvation_n
        self.credit = defaultdict(int)
        self.cursor = defaultdict(int)
        self.quantum_added = defaultdict(bool)
        self.age = defaultdict(int)
        self.inflight = defaultdict(int)
        self.lock = threading.RLock()
        for team in self.teams.values():
            if int(team.get("weight", 1)) <= 0:
                raise ValueError("team weights must be positive")
        if starvation_n < len(self.teams):
            raise ValueError("starvation N must cover the number of teams")

    def eligible(self, unit, lane):
        team = self.teams.get(unit.get("team_id"))
        if team is None or lane not in self.lanes:
            return False
        cfg = team.get("factory", {})
        cap = int(cfg.get("max_inflight_per_lane", 1))
        if self.inflight[lane, unit["team_id"]] >= cap:
            return False
        if unit.get("data_class") == "restricted" and not self.lanes[lane].get("local") is True:
            return False
        return lane in unit.get("lanes", [lane])

    def choose(self, units, lane):
        with self.lock:
            ready = defaultdict(list)
            for unit in units:
                if self.eligible(unit, lane):
                    ready[unit["team_id"]].append(unit)
            if not ready:
                return None
            for team_id in self.teams:
                if team_id not in ready:
                    self.age[lane, team_id] = 0
                    self.credit[lane, team_id] = 0
                    self.quantum_added[lane, team_id] = False
            order = sorted(self.teams)
            guarded = [t for t in ready if self.age[lane, t] >= self.starvation_n - 1]
            if guarded:
                chosen = max(guarded, key=lambda t: (self.age[lane, t], t))
                # Guard service is charged to its next quantum, not free extra share.
                self.credit[lane, chosen] -= 1
            else:
                while True:
                    index = self.cursor[lane] % len(order)
                    chosen = order[index]
                    if chosen not in ready:
                        self.cursor[lane] += 1
                        self.quantum_added[lane, chosen] = False
                        continue
                    if not self.quantum_added[lane, chosen]:
                        self.credit[lane, chosen] += int(self.teams[chosen].get("weight", 1))
                        self.quantum_added[lane, chosen] = True
                    if self.credit[lane, chosen] >= 1:
                        self.credit[lane, chosen] -= 1
                        if self.credit[lane, chosen] < 1:
                            self.cursor[lane] += 1
                            self.quantum_added[lane, chosen] = False
                        break
                    self.cursor[lane] += 1
                    self.quantum_added[lane, chosen] = False
            for team_id in ready:
                self.age[lane, team_id] = 0 if team_id == chosen else self.age[lane, team_id] + 1
            ceiling = int(self.teams[chosen].get("factory", {}).get("queue_priority_ceiling", 0))
            unit = min(ready[chosen], key=lambda u: (-min(int(u.get("priority", 0)), ceiling), u.get("seq", 0)))
            self.inflight[lane, chosen] += 1
            return unit

    def release(self, lane, team_id):
        with self.lock:
            if self.inflight[lane, team_id] <= 0:
                raise ValueError("lane reservation is not active")
            self.inflight[lane, team_id] -= 1


def route_escalation(base_url, team_id, task_class, data_class, token=None):
    """Only pace may authorize API/pool escalation; restricted stays local."""
    from urllib.request import Request, urlopen
    if data_class == "restricted":
        return []
    payload = json.dumps({"team": team_id, "task_class": task_class, "data_class": data_class}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(base_url.rstrip("/") + "/v1/route", payload, headers, method="POST")
    with urlopen(request, timeout=10) as response:
        data = response.read(65537)
    if len(data) > 65536:
        raise ValueError("pace response exceeds limit")
    accounts = json.loads(data).get("accounts", [])
    if any(a.get("type") == "seat" for a in accounts):
        raise ValueError("pace returned an interactive seat")
    return accounts
