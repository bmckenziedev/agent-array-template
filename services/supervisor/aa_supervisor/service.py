"""Command dispatcher with authority checked at execution time."""
import datetime
import hashlib
import json
import os
import time
import threading
from functools import wraps
from pathlib import Path

from .egress import Egress
from .policy import Limits, authorize, classify
from .runtime import Registry, Tmux, validate_cwd, output_payload
from .pace import Pace, LeaseDenied


class PluginContext:
    """Delivery plugins receive only the documented restricted methods."""
    __slots__ = ("audit", "post_json")

    def __init__(self, audit, post_json):
        self.audit = audit
        self.post_json = post_json


def synchronized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return guarded


class Supervisor:
    def __init__(self, env=None, egress=None, tmux=None, state_path=None):
        self.env = dict(os.environ if env is None else env)
        self.lock = threading.RLock()
        self.egress = egress or Egress()
        self.registry = Registry(self.env, tmux or Tmux())
        self.policy = {}
        self.policy_hash = None
        self.limits = Limits()
        self.pending = {}
        self.seq = 0
        self.counters = {}
        self.pace = Pace(self.env, self.egress)
        self.leases = {}
        self.verified = json.loads(Path(__file__).with_name("verified.json").read_text())
        self.links_path = Path("/run/aa-supervisor/links.json")
        self.recovery_policy = {}
        self.links = {}
        self.last_output = {}
        try:
            from .egress import safe_open
            fd = safe_open(self.links_path)
            try:
                self.links = json.loads(os.read(fd, 65536))
            finally:
                os.close(fd)
            if not isinstance(self.links, dict):
                self.links = {}
        except (OSError, ValueError):
            pass
        self.state_path = Path(state_path or "/run/aa-supervisor/state.json")
        self.state_loaded = False
        try:
            from .egress import safe_open
            fd = safe_open(self.state_path)
            try:
                snapshot = json.loads(os.read(fd, 262145))
            finally:
                os.close(fd)
            self.restore_state(snapshot)
        except (OSError, ValueError, KeyError, TypeError):
            for identity in list(self.registry.fifos):
                self.registry.close_input(identity)
            self.registry.spawned, self.leases = {}, {}

    def restore_state(self, snapshot):
        import re
        restored = {}
        for identity, record in snapshot.get("sessions", {}).items():
            if not re.fullmatch(r"[a-f0-9]{16}\.[a-f0-9]{16}", identity):
                raise ValueError("invalid saved identity")
            if any(record.get(k) != self.env.get(e) for k, e in [
                ("owner", "AA_USER"), ("team", "AA_TEAM"), ("account", "AA_ACCOUNT")]):
                raise ValueError("saved session belongs to another entity")
            if not re.fullmatch(r"%[0-9]+", record.get("pane", "")):
                raise ValueError("invalid saved handle")
            if record.get("tool") not in {"claude", "codex"} or record.get("kind") != "spawned":
                raise ValueError("invalid saved tool")
            if not re.fullmatch(r"/run/aa/spawn-[a-f0-9]{16}\.[a-f0-9]{16}/" + re.escape(identity),
                                str(record.get("directory", ""))):
                raise ValueError("invalid saved directory")
            restored[identity] = record
        self.registry.spawned = restored
        self.leases = {identity: {"lease": lease["lease"], "renew_at": 0}
                       for identity, lease in snapshot.get("leases", {}).items() if identity in restored}
        self.seq = max(self.seq, int(snapshot.get("seq", 0)))
        for identity, record in restored.items():
            if record["state"] == "exited":
                continue
            if record["state"] == "waiting-permission":
                record["state"] = "running"
            try:
                from .egress import safe_open
                if record["tool"] != "claude" or not self.verified.get("claude"):
                    raise OSError("unverified restored handle")
                fd = safe_open(Path(record["directory"]) / "input", os.O_RDWR | os.O_NONBLOCK, fifo=True)
                self.registry.fifos[identity] = fd
            except (OSError, AttributeError):
                record["drivable"], record["drive_via"] = "signal-only", "tmux"
            if not self.verified.get("claude_permission"):
                record["permission_routing"] = "unavailable"
        self.state_loaded = True

    def save_state(self):
        # A private journal proves prior creation; shared-volume ownership never does.
        if self.registry.parent is None and not self.state_loaded:
            return
        self.egress.save_json(self.state_path, {"sessions": {identity: record for identity, record in self.registry.spawned.items()
                                                            if record["state"] != "exited"},
                                               "leases": self.leases, "seq": self.seq})

    def count(self, name, labels=None):
        key = name
        if labels:
            key += "{" + ",".join(k + "=" + json.dumps(v) for k, v in sorted(labels.items())) + "}"
        self.counters[key] = self.counters.get(key, 0) + 1

    def emit(self, identity, kind, payload):
        self.seq += 1
        frame = {"id": identity, "seq": self.seq, "kind": kind, "redacted": True, "payload": payload}
        if getattr(self, "connector", None):
            self.connector.enqueue(frame)
        return frame

    def reject_input(self, reason, actor):
        self.audit("supervisor.input_rejected", "deny", actor, {"reason": reason})
        self.count("aa_supervisor_input_rejected_total", {"reason": reason})
        identity = actor.get("id")
        if not self.limits.accept(("rejected", identity), 4) and self.limits.accept(("burst", identity), 1):
            self.notify({"type": "input.rejected_burst"})

    @synchronized
    def snapshot_metrics(self, records=None):
        records = self.registry.discover(self.policy) if records is None else records
        gauges = {}
        for record in records.values():
            key = "aa_supervisor_sessions{" + ",".join(
                k + "=" + json.dumps(record[k]) for k in ["kind", "tool", "state"]) + "}"
            gauges[key] = gauges.get(key, 0) + 1
        return {"aa_supervisor_redactions_total": self.egress.count,
                "aa_supervisor_breakglass_total": 0,
                "aa_supervisor_connector_up": int(bool(getattr(self, "connector", None) and self.connector.up)),
                **self.counters, **gauges}

    def spawn(self, request):
        tool = request["tool"]
        cwd = validate_cwd(request.get("cwd", "/work"))
        brief = request.get("brief")
        rejection = self.limits.input("spawn", brief, self.policy)
        if rejection:
            self.reject_input(rejection, {"kind": "local", "id": self.env.get("AA_USER")})
            return {"error": "gate_rejected"}
        estate = request.get("estate_id")
        import re
        if estate is not None and (not isinstance(estate, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", estate)):
            return {"error": "gate_rejected"}
        work_item = request.get("work_item")
        if work_item is not None:
            self.validate_work_item(work_item)
        if not self.verified.get(tool) or not self.verified.get("tmux_argv"):
            return {"error": "not_drivable", "detail": "VERIFY launch contract required"}
        advisories, lease = [], None
        try:
            advisories = self.pace.advisory(self.policy)
        except (OSError, ValueError, KeyError):
            advisories = [{"account": self.env.get("AA_ACCOUNT"), "reason": "pace_unreachable"}]
        try:
            lease = self.pace.acquire()
            if not isinstance(lease.get("lease_id"), str) or not lease["lease_id"]:
                raise LeaseDenied("invalid_response")
            self.audit("session.lease")
        except LeaseDenied as exc:
            self.audit("session.lease", "deny", detail={"reason": str(exc)})
            return {"error": "lease_denied", "reason": str(exc)}
        except (OSError, ValueError, KeyError):
            self.audit("session.lease", "error", detail={"reason": "pace_unreachable"})
            if self.env.get("AA_LEASE_FAIL_CLOSED", "false").lower() in {"1", "true"}:
                return {"error": "pace_unreachable"}
            if not any(a["reason"] == "pace_unreachable" for a in advisories):
                advisories.append({"account": self.env.get("AA_ACCOUNT"), "reason": "pace_unreachable"})
        try:
            record = self.registry.launch(tool, cwd, brief, self.egress, work_item, estate, self.policy["stop_grace_s"])
            record["permission_routing"] = "available" if tool == "claude" and self.verified.get("claude_permission") else "unavailable"
        except Exception:
            if lease:
                self.pace.release(lease["lease_id"])
            raise
        if lease:
            self.leases[record["id"]] = {"lease": lease, "renew_at": time.monotonic() + 60}
        for advisory in advisories:
            self.seq += 1
            frame = {"id": record["id"], "seq": self.seq, "kind": "advisory", "redacted": True, "payload": advisory}
            self.audit("session.advisory", detail={"reason": advisory.get("reason")})
            if getattr(self, "connector", None):
                self.connector.enqueue(frame)
        record["started_notified"] = False
        self.save_state()
        return {"id": record["id"], "session_id": record["session_id"], "advisories": advisories, "lease": lease}

    @staticmethod
    def validate_work_item(item):
        from urllib.parse import urlsplit
        if not isinstance(item, dict) or set(item) != {"system", "id", "url"}:
            raise ValueError("invalid work item")
        if any(not isinstance(v, str) or not v or len(v) > 2048 for v in item.values()):
            raise ValueError("invalid work item")
        url = urlsplit(item["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("invalid work item URL")

    @synchronized
    def tick(self):
        self.reload()
        for rid, item in self.pending.items():
            if item.get("decision") == "pending" and time.monotonic() >= item["deadline"]:
                item["decision"] = "deny"
                self.audit("permission.decision", "deny", detail={"decision": "deny", "reason": "timeout"})
                self.count("aa_supervisor_permission_total", {"tier": "human", "decision": "deny"})
                session = self.registry.spawned.get(item.get("session"))
                if session:
                    session["state"] = "running"
                self.notify({"type": "permission.decided", "request_id": rid, "decision": "deny"})
        try:
            active = {p["pane"] for p in self.registry.tmux.panes()}
        except Exception:
            return
        for identity, record in self.registry.spawned.items():
            if record["state"] == "exited":
                continue
            try:
                text = self.registry.output(record)
                for line in text.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    sid = event.get("session_id") or event.get("thread_id")
                    if isinstance(sid, str):
                        record["session_id"] = sid
                        if not record.get("started_notified"):
                            self.notify({"type": "session.started", "session_id": sid,
                                         "work_item": record.get("work_item"), "summary": record["tool"] + " session started"})
                            record["started_notified"] = True
            except OSError:
                pass
            lease = self.leases.get(identity)
            if record["pane"] not in active:
                record["state"] = "exited"
                self.registry.close_input(identity)
                if lease:
                    try:
                        self.pace.release(lease["lease"]["lease_id"])
                        self.audit("session.lease_release")
                    except Exception:
                        self.audit("session.lease_release", "error")
                    self.leases.pop(identity, None)
                self.notify({"type": "session.exited", "session_id": record["session_id"],
                             "work_item": record["work_item"], "summary": record["tool"] + " session exited"})
                self.emit(identity, "exit", {"state": "exited"})
            elif lease and time.monotonic() >= lease["renew_at"]:
                try:
                    self.pace.renew(lease["lease"]["lease_id"])
                    self.audit("session.lease_renew")
                except Exception as exc:
                    self.audit("session.lease_renew", "error")
                    if isinstance(exc, LeaseDenied) or self.env.get("AA_LEASE_FAIL_CLOSED", "false") == "true":
                        self.audit("supervisor.stop_emergency", detail={"reason": "lease_renewal_refused"})
                        self.count("aa_supervisor_stops_total", {"emergency": "true"})
                        self.notify({"type": "stop.emergency", "reason": "lease_renewal_refused"})
                        self.registry.tmux.run("send-keys", "-t", record["pane"], "C-c")
                        record["stop_at"] = time.monotonic() + self.policy.get("stop_grace_s", 10)
                lease["renew_at"] = time.monotonic() + 60
            if record.get("stop_at") is not None and time.monotonic() >= record["stop_at"]:
                self.registry.close_input(identity)
                self.registry.tmux.run("kill-pane", "-t", record["pane"])
                record.pop("stop_at", None)
        if getattr(self, "connector", None):
            for record in self.registry.discover(self.policy).values():
                try:
                    text = self.registry.output(record)
                    previous = self.last_output.get(record["id"], "")
                    if text != previous:
                        payload = text[len(previous):] if text.startswith(previous) else text
                        kind, payload = output_payload(record, payload)
                        self.seq += 1
                        self.connector.enqueue({"type": "sessions.output", "id": record["id"],
                            "seq": self.seq, "kind": kind, "redacted": True, "payload": payload})
                        self.last_output[record["id"]] = text
                except Exception:
                    self.audit("supervisor.output", "error")
        self.save_state()

    def audit(self, event, outcome="allow", actor=None, detail=None):
        actor = actor or {}
        self.egress.send({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "component": "supervisor", "event": event, "actor": {"user": actor.get("id"),
            "sa": None, "sub": actor.get("sub"), "kind": actor.get("kind")},
            "team": self.env.get("AA_TEAM"), "target": {}, "outcome": outcome, "detail": detail or {}})

    @synchronized
    def reload(self):
        try:
            raw = Path(self.env.get("AA_SUPERVISOR_POLICY", "/etc/aa-supervisor/policy.json")).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest == self.policy_hash:
                return
            policy = json.loads(raw)
            if policy["holder"]["slug"] != self.env.get("AA_USER"):
                raise ValueError("holder mismatch")
            self.validate_policy(policy)
            redactor = Egress(policy.get("redact_extra_patterns", []), extra_names=policy.get("secret_key_names", []))
            self.policy, self.policy_hash = policy, digest
            self.recovery_policy = policy
            self.egress.patterns, self.egress.secret_names = redactor.patterns, redactor.secret_names
            self.audit("supervisor.policy_reload")
        except Exception:
            self.policy, self.policy_hash = {}, None

    @staticmethod
    def validate_policy(policy):
        from .policy import CATEGORIES
        import re
        holder = policy["holder"]
        if holder["status"] not in {"active", "suspended"} or not isinstance(holder["oidc_sub"], str):
            raise ValueError("invalid holder")
        if not isinstance(holder["teams"], list) or not isinstance(holder["primary_team"], str):
            raise ValueError("invalid holder teams")
        for key in ["input_max_bytes", "input_max_per_minute", "stops_per_minute_session",
                    "stops_per_minute_user", "stop_grace_s", "spawn_advisory_margin_pct",
                    "spawn_advisory_stale_s", "human_timeout_s", "policy_hook_timeout_s", "hook_input_max_bytes", "metrics_push_s"]:
            if not isinstance(policy[key], int) or isinstance(policy[key], bool) or policy[key] < 0:
                raise ValueError("invalid limit")
        for key in ["automation_identities", "spawn_refused_accounts", "spawn_refused_users", "policy_may_allow"]:
            if not isinstance(policy[key], list) or any(not isinstance(v, str) for v in policy[key]):
                raise ValueError("invalid identity list")
        for key in ["leads_can_view", "drive_discovered"]:
            if not isinstance(policy[key], bool):
                raise ValueError("invalid flag")
        for category in CATEGORIES:
            if policy["permission_tiers"][category] not in {"auto", "policy", "human"}:
                raise ValueError("invalid tier")
            if not isinstance(policy["deciders"][category], list):
                raise ValueError("invalid deciders")
        for rule in policy["classify_extra"]:
            if rule["category"] not in CATEGORIES:
                raise ValueError("invalid classification")
            re.compile(rule["arg_regex"])

    def notify(self, event):
        from importlib import import_module
        for config in self.policy.get("notifiers", []):
            name = config.get("type")
            import re
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                self.audit("supervisor.notify", "error")
                continue
            try:
                ctx = PluginContext(self.audit, self.post_json)
                result = import_module("aa_supervisor.notifiers." + name).Plugin(config, ctx).notify(event)
                self.audit("supervisor.notify", "allow" if result["delivered"] else "error")
                self.count("aa_supervisor_notify_total", {"type": name,
                    "outcome": "allow" if result["delivered"] else "error"})
            except Exception:
                self.audit("supervisor.notify", "error")
        if event.get("type") in {"session.started", "session.exited", "permission.waiting"}:
            if not event.get("session_id"):
                return
            for config in self.policy.get("work_item_adapters", []):
                if config.get("type") != "webhook":
                    self.audit("supervisor.adapter", "deny")
                    continue
                try:
                    ctx = PluginContext(self.audit, self.post_json)
                    import_module("aa_supervisor.adapters.webhook").Plugin(config, ctx).notify(event)
                    self.audit("supervisor.adapter")
                except Exception:
                    self.audit("supervisor.adapter", "error")

    def post_json(self, url, payload, timeout):
        if url == "console":
            if not getattr(self, "connector", None):
                raise OSError("connector disabled")
            self.connector.enqueue(payload)
            return {}
        return self.egress.post_json(url, payload, timeout,
            "/var/run/agent-array/supervisor-hook-token/token")

    @synchronized
    def permission(self, request):
        try:
            result = self._permission(request)
        except Exception:
            result = {"decision": "deny", "error": "gate_rejected"}
        self.audit("permission.request", "deny" if result.get("decision") == "deny" else "allow",
                   detail={"category": result.get("category", "unknown")})
        if result.get("decision") == "deny" and "category" not in result:
            self.audit("permission.decision", "deny", detail={"decision": "deny", "reason": result.get("error")})
        return result

    def _permission(self, request):
        self.reload()
        if not self.policy:
            return {"decision": "deny", "error": "policy_unavailable"}
        session = next((r for r in self.registry.spawned.values()
                        if r["id"] == request.get("session_id") and r.get("state") != "exited"
                        and r.get("permission_routing") == "available"), None)
        if session is None:
            return {"decision": "deny", "error": "not_found"}
        rid = request.get("request_id")
        if not isinstance(rid, str) or not rid or len(rid) > 128:
            return {"decision": "deny"}
        category = classify(request.get("tool", ""), request.get("arguments", {}), self.policy.get("classify_extra", []))
        fingerprint = hashlib.sha256(json.dumps({k: request.get(k) for k in
            ["session_id", "tool", "arguments"]}, sort_keys=True).encode()).hexdigest()
        if rid in self.pending and self.pending[rid].get("fingerprint") != fingerprint:
            return {"decision": "deny", "error": "gate_rejected"}
        if rid in self.pending:
            return self.pending[rid]
        tier = self.policy["permission_tiers"].get(category, "policy")
        arguments = request.get("arguments", {})
        fields = []
        if isinstance(arguments, dict):
            for key in ["command", "file_path", "path", "url", "amount", "action", "target"]:
                if key in arguments:
                    fields.append(key + "=" + str(arguments[key]))
        summary = self.egress.redact(str(request.get("summary", request.get("tool", ""))) + " " + ", ".join(fields))
        summary = summary.encode()[:self.policy["hook_input_max_bytes"]].decode("utf-8", "ignore")
        # The category floor cannot be weakened by a policy hook.
        if category in {"money", "outside-contact", "irreversible", "access-change"}:
            tier = "human" if tier == "auto" else tier
        decision = "allow" if tier == "auto" else "escalate"
        if tier == "policy" and self.policy.get("policy_hook_url"):
            try:
                decision = self.post_json(self.policy["policy_hook_url"], {"category": category, "summary": summary},
                                          self.policy["policy_hook_timeout_s"]).get("decision", "escalate")
            except Exception:
                decision = "escalate"
            if category in {"money", "outside-contact", "irreversible", "access-change"} and category not in self.policy["policy_may_allow"] and decision == "allow":
                decision = "escalate"
        if decision not in {"allow", "deny"}:
            item = {"request_id": rid, "category": category, "decision": "pending",
                    "deadline": time.monotonic() + self.policy["human_timeout_s"], "fingerprint": fingerprint,
                    "summary": summary}
            self.pending[rid] = item
            item["session"] = session["id"]
            session["state"] = "waiting-permission"
            self.emit(session["id"], "permission", {"request_id": rid, "category": category, "summary": summary})
            self.notify({"type": "permission.human_needed", "request_id": rid,
                         "category": category, "summary": summary})
            self.notify({"type": "permission.waiting", "session_id": session.get("session_id"),
                         "work_item": session.get("work_item"), "summary": summary})
            return item
        self.audit("permission.decision", decision, detail={"decision": decision, "tier": tier})
        self.emit(session["id"], "permission", {"request_id": rid, "category": category, "decision": decision})
        self.count("aa_supervisor_permission_total", {"tier": tier, "decision": decision})
        self.pending[rid] = {"decision": decision, "category": category, "fingerprint": fingerprint}
        return self.pending[rid]

    @synchronized
    def execute(self, request, local=False):
        self.reload()
        if not isinstance(request, dict):
            self.audit("supervisor.command", "deny")
            return {"error": "gate_rejected"}
        command = request.get("command", "")
        if not isinstance(command, str):
            command = ""
        actor = {"kind": "local", "id": self.env.get("AA_USER")} if local else request.get("actor", {})
        if not isinstance(actor, dict):
            actor = {}
        result = {"error": "forbidden"}
        failed = False
        try:
            if not local and actor.get("kind") == "local":
                return result
            policy = self.policy
            if not policy:
                # Local exec retains observe/stop recovery; no remote identity can be verified.
                if command not in {"sessions.list", "sessions.output", "sessions.stop", "metrics"}:
                    result = {"error": "policy_unavailable"}
                    return result
                policy = self.recovery_policy or ({"holder": {"slug": self.env.get("AA_USER")}} if local else {})
            rid = request.get("request_id")
            category = self.pending.get(rid, {}).get("category") if isinstance(rid, str) else None
            error = authorize(policy, actor, command, request.get("ticket"), category, bool(self.env.get("AA_USER")))
            if error:
                result = {"error": error}
                return result
            if not isinstance(request.get("emergency", False), bool):
                result = {"error": "gate_rejected"}
                return result
            if request.get("protocol_version", 1) != 1:
                result = {"error": "unsupported_version"}
                return result
            if policy.get("breakglass_group") in actor.get("groups", []) and request.get("ticket"):
                self.audit("supervisor.breakglass", actor=actor, detail={"ticket": request["ticket"]})
                self.count("aa_supervisor_breakglass_total")
            records = self.registry.discover(policy)
            for record in records.values():
                if record.get("session_id") in self.links:
                    record["work_item"] = self.links[record["session_id"]]
            if command == "sessions.list":
                result = {"sessions": list(records.values())}
            elif command == "metrics":
                result = self.snapshot_metrics(records)
            elif command == "sessions.spawn":
                if self.env.get("AA_ACCOUNT") in policy["spawn_refused_accounts"] or self.env.get("AA_USER") in policy["spawn_refused_users"]:
                    result = {"error": "account_refused"}
                elif sum(r["state"] != "exited" for r in records.values()) >= int(self.env.get("AA_MAX_SESSIONS", "1")):
                    result = {"error": "rate_limited", "detail": "max_sessions"}
                elif request.get("tool") not in {"claude", "codex"} or (
                    self.env.get("AA_TOOL") and request.get("tool") != self.env["AA_TOOL"]):
                    result = {"error": "unsupported_tool"}
                else:
                    result = self.spawn(request)
            elif command == "permission.decide":
                item = self.pending.get(request.get("request_id"))
                decision = request.get("decision")
                if not item:
                    result = {"error": "not_found"}
                elif item["decision"] != "pending":
                    result = {"decision": item["decision"]}
                elif decision not in {"allow", "deny"}:
                    result = {"error": "gate_rejected"}
                else:
                    item["decision"] = "deny" if time.monotonic() >= item.get("deadline", float("inf")) else decision
                    self.audit("permission.decision", item["decision"], actor=actor, detail={"decision": item["decision"]})
                    self.count("aa_supervisor_permission_total", {"tier": "human", "decision": item["decision"]})
                    result = {"decision": item["decision"]}
                    session = self.registry.spawned.get(item.get("session"))
                    if session:
                        session["state"] = "running"
                    self.notify({"type": "permission.decided", "request_id": request.get("request_id"), "decision": item["decision"]})
            else:
                record = records.get(request.get("id"))
                if not record:
                    result = {"error": "not_found"}
                elif command == "sessions.output":
                    self.seq += 1
                    pending = next((p for p in self.pending.values() if p.get("session") == record["id"]
                                    and p.get("decision") == "pending"), None)
                    kind, payload = output_payload(record, self.registry.output(record)) if pending is None else (None, None)
                    result = {"id": record["id"], "seq": self.seq, "kind": kind, "redacted": True,
                              "payload": payload} if pending is None else {
                                  "id": record["id"], "seq": self.seq, "kind": "permission", "redacted": True,
                                  "payload": {k: pending[k] for k in ["request_id", "category", "summary"]}}
                elif command == "sessions.input":
                    rejection = self.limits.input(record["id"], request.get("text"), policy)
                    if rejection:
                        self.reject_input(rejection, actor)
                        result = {"error": "gate_rejected"}
                    elif record["drivable"] != "full":
                        result = {"error": "not_drivable"}
                    else:
                        if record["kind"] == "spawned":
                            self.registry.input_spawned(record["id"], request["text"], self.egress)
                        else:
                            self.registry.tmux.input(record["pane"], self.egress.redact(request["text"]))
                        result = {"ok": True}
                elif command in {"sessions.stop", "sessions.interrupt"}:
                    if not isinstance(request.get("reason"), str) or not request["reason"].strip():
                        result = {"error": "gate_rejected"}
                    elif record["drivable"] == "none":
                        result = {"error": "not_drivable"}
                    elif not request.get("emergency") and not self.limits.accept_many([
                        (("stop", record["id"]), policy.get("stops_per_minute_session", 6)),
                        (("user-stop", record["owner"]), policy.get("stops_per_minute_user", 20))]):
                        result = {"error": "rate_limited"}
                    else:
                        if request.get("emergency"):
                            self.audit("supervisor.stop_emergency", actor=actor)
                            self.notify({"type": "stop.emergency"})
                        self.count("aa_supervisor_stops_total", {"emergency": "true" if request.get("emergency") else "false"})
                        self.registry.tmux.run("send-keys", "-t", record["pane"], "C-c")
                        if command == "sessions.stop":
                            self.registry.close_input(record["id"])
                            time.sleep(policy.get("stop_grace_s", 10))
                            self.registry.tmux.run("kill-pane", "-t", record["pane"])
                        result = {"ok": True}
                elif command == "sessions.link":
                    sid = request.get("session_id")
                    if not isinstance(sid, str) or not sid or len(sid) > 128:
                        result = {"error": "gate_rejected"}
                    elif record.get("session_id") != sid:
                        result = {"error": "not_found"}
                    else:
                        self.validate_work_item(request["work_item"])
                        record["work_item"] = request["work_item"]
                        self.links[sid] = request["work_item"]
                        self.links_path.parent.mkdir(exist_ok=True)
                        from .egress import safe_open
                        fd = safe_open(self.links_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                        try:
                            self.egress.write_fd(fd, self.egress.encode(self.links))
                        finally:
                            os.close(fd)
                        result = {"ok": True}
                else:
                    result = {"error": "not_drivable"}
            return result
        except ValueError as exc:
            result = {"error": "not_drivable" if str(exc) == "not_drivable" else "gate_rejected"}
            return result
        except Exception:
            failed = True
            result = {"error": "gate_rejected"}
            return result
        finally:
            known = {"sessions.list", "sessions.output", "sessions.spawn", "sessions.input", "sessions.interrupt",
                     "sessions.stop", "sessions.link", "permission.decide", "metrics"}
            outcome = "error" if failed else "deny" if "error" in result else "allow"
            self.audit(command if command in known else "supervisor.command", outcome, actor,
                       {"error": result.get("error")})
            if command == "sessions.spawn":
                self.audit("session.spawn", outcome, actor,
                           {"error": result.get("error")})
                self.count("aa_supervisor_spawn_total", {"outcome": outcome})
