"""One supervised foreground HTTP service; no agent-spawned workers."""

import argparse
import asyncio
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .auth import Authenticator, Denied, Directory, Kubernetes

MAX_BODY = 1024 * 1024
BATCH_PATH = re.compile(r"^/v1/batches/([a-zA-Z0-9_-]{1,128})(?:/(approve|cancel))?$")


def public_batch(batch, include_results=False):
    """Avoid multiplying submitted payloads across a bounded list response."""
    fields = ("batch_id", "team_id", "submitted_by", "data_class", "estate_id",
              "status", "priority", "created")
    result = {key: batch[key] for key in fields if key in batch}
    if include_results:
        value = batch.get("result")
        if len(json.dumps(value).encode()) > 512 * 1024:
            result["result"] = {"truncated": True, "reason": "result exceeds API output cap"}
        else:
            result["result"] = value
    return result


def audit(event, identity=None, team=None, target=None, outcome="allow", detail=None):
    actor = identity or {}
    print(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "component": "factory", "event": event,
        "actor": {k: actor.get(k) for k in ("user", "sa", "sub")},
        "team": team, "target": target or {}, "outcome": outcome, "detail": detail or {},
    }, sort_keys=True), flush=True)


class FactoryServer(HTTPServer):
    def __init__(self, address, auth, store, metrics=None):
        super().__init__(address, FactoryHandler)
        self.auth = auth
        self.store = store
        self.metrics = metrics
        self.lock = threading.RLock()


class FactoryHandler(BaseHTTPRequestHandler):
    server_version = "FactoryAPI/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def refresh_policy(self, directory):
        """Revoked directory permissions take effect before every mutation."""
        store = self.server.store
        if hasattr(store, "teams"):
            teams = directory["teams"]
            store.teams = {t["id"]: t for t in teams} if isinstance(teams, list) else teams
        if hasattr(store, "estates"):
            estates = directory["estates"]
            store.estates = estates.get("estates", []) if isinstance(estates, dict) else estates
            if hasattr(store, "deny_globs") and isinstance(estates, dict):
                store.deny_globs = estates.get("deny_globs", [])

    def log_message(self, *_):
        # Access logs must never include bearer tokens or submitted content.
        pass

    def respond(self, status, body, content_type="application/json"):
        raw = json.dumps(body).encode() if content_type == "application/json" else body.encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # A client disconnect must not suppress the decision's audit event.
            self.close_connection = True

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("transfer encoding is unsupported")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY:
            raise ValueError("body must be between 1 byte and 1 MiB")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("application/json required")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        identity, team, target = None, None, {}
        parsed = urlsplit(self.path)
        event = "request.factory"
        try:
            if method == "GET" and parsed.path == "/healthz":
                self.respond(200, {"status": "ok"})
                return
            if method == "GET" and parsed.path == "/metrics":
                with self.server.lock:
                    value = self.server.metrics() if self.server.metrics else (
                        "# HELP aa_factory_api_up Factory API availability\n"
                        "# TYPE aa_factory_api_up gauge\naa_factory_api_up 1\n"
                    )
                self.respond(200, value, "text/plain; version=0.0.4")
                return
            identity, directory = self.server.auth.authenticate(self.headers.get("Authorization", ""))
            if parsed.path == "/v1/batches":
                if method == "POST":
                    event = "submit.batch"
                    payload = self.body()
                    if set(payload) - {"estate_id", "template", "cards", "priority", "team"}:
                        raise ValueError("unknown batch fields; identity is derived from token")
                    team = payload.get("team", identity["primary_team"])
                    self.server.auth.authorize(identity, directory, team)
                    with self.server.lock:
                        self.refresh_policy(directory)
                        batch = self.server.store.submit(payload, identity)
                    target = {"batch_id": batch.get("batch_id", batch.get("id"))}
                    self.respond(202, {**target, "status": "pending-approval"})
                else:
                    event = "list.batch"
                    query = parse_qs(parsed.query)
                    if set(query) - {"team"} or len(query.get("team", [])) > 1:
                        raise ValueError("only one team query is supported")
                    team = query.get("team", [identity["primary_team"]])[0]
                    self.server.auth.authorize(identity, directory, team)
                    with self.server.lock:
                        batches = self.server.store.list(team, limit=100)
                    self.respond(200, {"batches": [public_batch(b) for b in batches], "limit": 100})
            else:
                match = BATCH_PATH.fullmatch(parsed.path)
                if not match:
                    self.respond(404, {"error": "route not found"})
                    audit(event, identity, outcome="deny", detail={"reason": "route"})
                    return
                batch_id, action = match.groups()
                target = {"batch_id": batch_id}
                with self.server.lock:
                    batch = self.server.store.get(batch_id)
                if batch is None:
                    raise KeyError(batch_id)
                team = batch.get("team_id", batch.get("team"))
                self.server.auth.authorize(identity, directory, team)
                event = (action or "status") + ".batch"
                with self.server.lock:
                    if method == "GET" and action is None:
                        value = public_batch(batch, include_results=True)
                        if hasattr(self.server.store, "events"):
                            value["events"] = self.server.store.events(batch_id)
                        self.respond(200, value)
                    elif method == "POST" and action in ("approve", "cancel"):
                        if action == "approve":
                            teams = directory["teams"]
                            if isinstance(teams, dict):
                                teams = teams.get("teams", [])
                            selected = next(t for t in teams if t["id"] == team)
                            if identity["user"] not in selected["leads"]:
                                raise Denied("team lead approval required")
                        self.refresh_policy(directory)
                        value = getattr(self.server.store, action)(batch_id, identity)
                        self.respond(200, public_batch(value) if value else
                                     {"batch_id": batch_id, "status": action})
                    else:
                        self.respond(405, {"error": "method not allowed"})
                        audit(event, identity, team, target, "deny", {"reason": "method"})
                        return
            audit(event, identity, team, target)
        except (Denied, PermissionError):
            self.respond(403, {"error": "identity or team permission denied"})
            audit(event, identity, team, target, "deny", {"reason": "permission"})
        except KeyError:
            self.respond(404, {"error": "batch not found"})
            audit(event, identity, team, target, "deny", {"reason": "missing"})
        except (ValueError, TypeError, StopIteration):
            self.respond(400, {"error": "invalid request or batch state"})
            audit(event, identity, team, target, "deny", {"reason": "validation"})
        except Exception:
            self.respond(503, {"error": "factory service unavailable"})
            audit(event, identity, team, target, "error", {"reason": "service"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--directory", default="/etc/agent-array/org")
    parser.add_argument("--state", default=str(Path(os.environ.get("FACTORY_STATE_DIR", "/work")) / "queue.sqlite"))
    parser.add_argument("--lanes", default="/etc/factory/lanes.json")
    parser.add_argument("--snapshot-root", default=os.environ.get("FACTORY_SNAPSHOT_ROOT"))
    args = parser.parse_args(argv)
    from factory_queue.api_store import BatchStore
    from factory_queue.worker import run_foreground
    from factory_engine.config import load_lanes
    from .metrics import Metrics

    directory = Directory(args.directory)
    config = directory.load()
    kube = Kubernetes(
        os.environ.get("APISERVER_URL", "https://kubernetes.default.svc"),
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
        "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
    )
    auth = Authenticator(
        kube, directory, os.environ["PROJECT_NAME"], os.environ["USER_NS_PREFIX"],
        os.environ["LABEL_PREFIX"],
        os.environ.get("OIDC_USERNAME_PREFIX", "oidc:"),
    )
    home = Path(args.state).parent
    snapshots = args.snapshot_root or str(home / "estates")
    caps = {"max_batches": int(os.environ.get("FACTORY_MAX_BATCHES", "32")),
            "max_cards": int(os.environ.get("FACTORY_MAX_CARDS", "100")),
            "max_payload_bytes": int(os.environ.get("FACTORY_MAX_PAYLOAD_BYTES", str(MAX_BODY)))}
    store = BatchStore(args.state, config["estates"], config["teams"], caps=caps,
                       snapshot_root=snapshots)
    lanes = load_lanes(Path(args.lanes))
    metrics = Metrics(home, lanes["lanes"], store)
    metrics.refresh()
    server = FactoryServer((args.host, args.port), auth, store, metrics=metrics.text)
    stop = threading.Event()
    server.timeout = 0.5
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    async def supervised():
        async def refresh_metrics():
            while not stop.is_set():
                await asyncio.to_thread(metrics.refresh)
                await asyncio.sleep(1)
        refresh = asyncio.create_task(refresh_metrics())
        try:
            await run_foreground(
                store, stop, home, lanes=args.lanes, snapshot_root=snapshots,
                starvation_n=int(os.environ.get("FACTORY_STARVATION_N", "20")),
                poll_seconds=int(os.environ.get("FACTORY_WORKER_POLL_SECONDS", "1")),
                retention_seconds=int(os.environ.get("FACTORY_RETENTION_SECONDS", "43200")),
                before_prune=metrics.flush,
            )
        finally:
            stop.set()
            await refresh

    def engine_worker():
        try:
            asyncio.run(supervised())
        except Exception:
            audit("error.worker", outcome="error", detail={"reason": "worker failed"})
            stop.set()

    worker = threading.Thread(target=engine_worker, name="factory-engine")
    worker.start()
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        stop.set()
        server.server_close()
        worker.join()
        metrics.refresh()
        metrics.close()
        close = getattr(store, "close", None)
        if close:
            close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
