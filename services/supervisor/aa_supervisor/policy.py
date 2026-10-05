"""Per-command authority, input limits and permission risk floor."""
import fnmatch
import re
import time
from collections import defaultdict, deque

CATEGORIES = ["read", "write-workspace", "network", "outside-contact", "irreversible", "money", "access-change"]
PATTERNS = {
    "read": r"\b(read|cat|list|grep|search)\b",
    "write-workspace": r"\b(write|edit|patch|mkdir)\b",
    "network": r"\b(fetch|curl|http|network|download)\b",
    "outside-contact": r"\b(send|publish|message|contact)\b",
    "irreversible": r"\b(delete|destroy|rm|drop|force)\b",
    "money": r"\b(pay|purchase|charge|billing|money)\b",
    "access-change": r"\b(grant|revoke|chmod|permission|credential|access)\b",
}


def classify(tool, args, extra=()):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", tool + " " + str(args)).replace("_", " ")
    matches = {c for c, p in PATTERNS.items() if re.search(p, text, re.I)}
    for rule in extra:
        if fnmatch.fnmatch(tool, rule["tool_glob"]) and re.search(rule["arg_regex"], str(args)):
            matches.add(rule["category"])
    return next((c for c in reversed(CATEGORIES) if c in matches), "unknown")


def authorize(policy, actor, command, ticket=None, category=None, owner=True):
    kind, identity = actor.get("kind"), actor.get("id")
    holder = policy.get("holder", {})
    principals = {p["slug"]: p for p in policy.get("principals", [])}
    known = {p["slug"]: p for p in policy.get("known_users", [])}
    local = kind == "local"
    if not local and (kind not in {"user", "automation"} or not isinstance(identity, str)
                      or not isinstance(actor.get("groups", []), list)
                      or any(not isinstance(g, str) for g in actor.get("groups", []))):
        return "forbidden"
    if local and holder.get("status", "active") != "active":
        return "forbidden"
    automation = kind == "automation" and identity in policy.get("automation_identities", [])
    person = holder if identity == holder.get("slug") else principals.get(identity, known.get(identity))
    if not local and not automation and not (
        kind == "user" and person and person.get("status") == "active"
        and actor.get("sub") == person.get("oidc_sub")
    ):
        return "forbidden"
    is_holder = owner and (local or person is holder)
    bg = bool(person and isinstance(ticket, str) and ticket.strip()
              and policy.get("breakglass_group") in actor.get("groups", []))
    lead = bool(person and policy.get("leads_can_view") and identity in policy.get("leads", []))
    if command == "sessions.spawn":
        return None if is_holder else "seat_interactive_only"
    if command in {"sessions.list", "sessions.output", "metrics"}:
        allowed = is_holder or automation or bg or lead
    elif command in {"sessions.input", "sessions.link"}:
        allowed = is_holder
    elif command == "sessions.interrupt":
        allowed = is_holder or bg
    elif command == "sessions.stop":
        allowed = is_holder or bg or (automation and owner)
    elif command == "permission.decide":
        allowed = is_holder or bool(not bg and person and identity in policy.get("deciders", {}).get(category, []))
    else:
        allowed = False
    return None if allowed else "forbidden"


class Limits:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.events = defaultdict(deque)

    def accept(self, key, cap):
        queue = self.events[key]
        now = self.clock()
        while queue and queue[0] <= now - 60:
            queue.popleft()
        if len(queue) >= cap:
            return False
        queue.append(now)
        return True

    def accept_many(self, limits):
        now = self.clock()
        queues = []
        for key, cap in limits:
            queue = self.events[key]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= cap:
                return False
            queues.append(queue)
        for queue in queues:
            queue.append(now)
        return True

    def input(self, session, text, policy):
        try:
            raw = text.encode("utf-8", errors="strict")
        except (UnicodeError, AttributeError):
            return "utf8"
        if len(raw) > policy["input_max_bytes"]:
            return "size"
        if any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127 for c in text):
            return "control"
        if not self.accept(("input", session), policy["input_max_per_minute"]):
            return "rate"
        return None
