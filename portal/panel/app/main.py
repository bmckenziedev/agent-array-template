#!/usr/bin/env python3
"""Identity-aware panel with socket-separated public and task callbacks.

Uploads are bounded and streamed; every task has a short-lived model key.
State has one writer on an RWO volume, with terminal revocation and retention.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import http.client
import io
import json
import logging
import math
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import tarfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from zlib import error as zlib_error
from pathlib import Path
from urllib.parse import urlsplit

import anyio
from identity import AccessDenied, Directory, TokenVerifier
import yaml
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response, StreamingResponse)
from kubernetes import client, config
from starlette.background import BackgroundTask
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("panel")


# --------------------------------------------------------------------------- #
# Configuration (env; safe defaults for the deployment). Auth settings are
# REQUIRED: the panel refuses to start without them (fail closed).
# --------------------------------------------------------------------------- #
def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _required(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v or "REPLACE" in v.upper():
        raise SystemExit(f"FATAL: {name} is not configured (see panel/README.md); "
                         "the panel refuses to start without identity validation")
    return v


def _origin(value: str) -> str:
    """scheme://host[:port], lower-case, default port dropped. '' if unparsable."""
    try:
        u = urlsplit(value.strip())
        host, port = u.hostname, u.port
    except ValueError:
        return ""
    if u.scheme not in ("http", "https") or not host or u.path not in ("", "/") \
            or u.query or u.fragment or u.username:
        return ""
    default = {"http": 80, "https": 443}[u.scheme]
    return f"{u.scheme}://{host.lower()}" + (f":{port}" if port and port != default else "")


NS = os.environ.get("SESSION_NAMESPACE", "agent-array-session-jobs")
DATA = Path(os.environ.get("DATA_DIR", "/data"))
TASKS = DATA / "tasks"
TEMPLATE = Path(os.environ.get("JOB_TEMPLATE", "/app/template/task-job.template.yaml"))
INDEX_HTML = Path(os.environ.get("INDEX_HTML", str(Path(__file__).with_name("index.html"))))
SESSION_IMAGE = os.environ.get("SESSION_IMAGE", "")
SESSION_JOBS_ENABLED = os.environ.get("SESSION_JOBS_ENABLED", "false").lower() in ("true", "1")
SESSION_RUNTIME_CLASS = os.environ.get("SESSION_RUNTIME_CLASS", "kata")
LABEL_PREFIX = os.environ.get("LABEL_PREFIX", "agent-array.example.org")
TASK_MODES = ("standard", "bulk")

PUBLIC_PORT = _int("PUBLIC_PORT", 8080)
INTERNAL_PORT = _int("INTERNAL_PORT", 8081)

ACCESS_KIND = os.environ.get("ACCESS_KIND", "oidc-proxy")
if ACCESS_KIND not in ("oidc-proxy", "cloudflare-access"):
    raise SystemExit("unsupported ACCESS_KIND")
if ACCESS_KIND == "cloudflare-access":
    team = _required("CF_ACCESS_TEAM")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", team):
        raise SystemExit("invalid CF_ACCESS_TEAM")
    ACCESS_ISSUER = f"https://{team}.cloudflareaccess.com"
    ACCESS_AUD = _required("CF_ACCESS_AUD")
    JWKS_URL = ACCESS_ISSUER + "/cdn-cgi/access/certs"
else:
    ACCESS_ISSUER = _required("OIDC_ISSUER_URL")
    ACCESS_AUD = _required("OIDC_CLIENT_ID")
    JWKS_URL = None
if not ACCESS_ISSUER.startswith("https://"):
    raise SystemExit("identity issuer requires HTTPS")
PANEL_ORIGIN = _origin(_required("PANEL_ORIGIN"))
if not PANEL_ORIGIN:
    raise SystemExit("invalid PANEL_ORIGIN")
DIRECTORY = Directory(Path(os.environ.get("ORG_DIRECTORY", "/etc/agent-array/org")),
                      os.environ.get("OIDC_GROUPS_CLAIM", "groups"),
                      _required("GROUP_PLATFORM_ADMIN"), _required("GROUP_AUDITOR"), ACCESS_KIND)
VERIFIER = TokenVerifier(ACCESS_ISSUER, ACCESS_AUD, JWKS_URL)

# Size caps (enforced before parsing; see BodyLimit).
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_BYTES", 25 * 1024 * 1024)    # sum of uploaded files
MAX_UPLOAD_FILES = _int("MAX_UPLOAD_FILES", 5000)                # per task (archive or parts)
MAX_UNPACKED_BYTES = _int("MAX_UNPACKED_BYTES", 128 * 1024 * 1024)  # archive, uncompressed
MAX_ARCHIVE_RATIO = _int("MAX_ARCHIVE_RATIO", 100)               # uncompressed / compressed
MAX_TEST_CMD_CHARS = 300
MAX_BRIEF_CHARS = _int("MAX_BRIEF_CHARS", 16 * 1024)
MAX_BUNDLE_BYTES = _int("MAX_BUNDLE_BYTES", 64 * 1024 * 1024)
MAX_STATUS_BODY = _int("MAX_STATUS_BODY", 16 * 1024)
MAX_DETAIL_CHARS = 2000
MAX_CONCURRENT_UPLOADS = _int("MAX_CONCURRENT_UPLOADS", 4)       # per listener
# Concurrency caps.
MAX_ACTIVE_TASKS_PER_USER = _int("MAX_ACTIVE_TASKS_PER_USER", 3)
MAX_ACTIVE_TASKS_PER_TEAM = _int("MAX_ACTIVE_TASKS_PER_TEAM", 10)
if min(MAX_ACTIVE_TASKS_PER_USER, MAX_ACTIVE_TASKS_PER_TEAM) < 1:
    raise SystemExit("active task limits must be positive")
MAX_STREAMS_PER_TASK = _int("MAX_STREAMS_PER_TASK", 3)
MAX_STREAMS = _int("MAX_STREAMS", 64)
# A task that has not reached a terminal stage this long after dispatch is
# failed by the reconciler (Job activeDeadlineSeconds 3600 + grace).
EVENT_DEADLINE_SECONDS = _int("EVENT_DEADLINE_SECONDS", 3600 + 180)
# Retention (see module docstring).
RETAIN_AFTER_TERMINAL_SECONDS = _int("RETAIN_AFTER_TERMINAL_SECONDS", 24 * 3600)
RETAIN_AFTER_DOWNLOAD_SECONDS = _int("RETAIN_AFTER_DOWNLOAD_SECONDS", 3600)
RECONCILE_INTERVAL_SECONDS = _int("RECONCILE_INTERVAL_SECONDS", 30)
ORPHAN_DIR_SECONDS = 3600   # task dir without status.json (crash mid-create)
TOMBSTONE_SECONDS = _int("TOMBSTONE_SECONDS", 30 * 86400)        # purged ids answer 410 this long
TOMBSTONES = DATA / "purged.json"
# --------------------------------------------------------------------------- #
# Route-restricted mint credential stays in the panel process.
LITELLM_ADMIN_BASE = _required("LITELLM_ADMIN_BASE").rstrip("/")
if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?", LITELLM_ADMIN_BASE):
    raise SystemExit("invalid LITELLM_ADMIN_BASE")
LITELLM_MINT_KEY = os.environ.get("LITELLM_MINT_KEY", "")
if SESSION_JOBS_ENABLED and not re.fullmatch(r"sk-[A-Za-z0-9_-]{8,}", LITELLM_MINT_KEY):
    raise SystemExit("LITELLM_MINT_KEY is required when session-jobs is enabled")
TASK_ALLOWED_MODELS = tuple(os.environ.get("TASK_MODELS", "local-coder,local-coder-small").split(","))
if not TASK_ALLOWED_MODELS or any(not re.fullmatch(r"[A-Za-z0-9._:/-]+", m)
                                  or m in ("all-proxy-models", "no-default-models")
                                  for m in TASK_ALLOWED_MODELS):
    raise SystemExit("TASK_MODELS requires explicit model groups")
TASK_KEY_TTL_SECONDS = _int("TASK_DURATION_SECONDS", 3900)
LITELLM_ADMIN_TIMEOUT_SECONDS = _int("LITELLM_ADMIN_TIMEOUT_SECONDS", 10)
if min(TASK_KEY_TTL_SECONDS, LITELLM_ADMIN_TIMEOUT_SECONDS) < 1:
    raise SystemExit("task duration and mint timeout must be positive")

# Public API contract version (GET /api/meta). Bump on any client-visible change.
API_VERSION = 3
PANEL_BUILD = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]

# Stage vocabulary. queued/dispatched are set by the panel; done/needs-review
# ONLY by a bundle upload; the Job may report the rest, or error-<reason>.
TERMINAL_STAGES = {"done", "needs-review"}
JOB_STAGES = {"fetching", "framing", "spec", "coding", "verifying", "repairing", "bundling"}
ERROR_STAGE_RE = re.compile(r"error-[a-z0-9][a-z0-9-]{0,39}")
TASK_ID_RE = re.compile(r"[0-9a-f]{16}")
# What GET status / SSE / list return (never the job name, token or bookkeeping).
PUBLIC_FIELDS = ("task_id", "stage", "ts", "detail", "verified", "pct", "owner", "created_ts",
                 "title", "base_commit", "repos", "test_cmd", "mode", "cancelled",
                 "team", "account_id", "data_class", "approved_by", "approved_ts")
# test_cmd: one line, a conservative charset with NO shell metacharacters; the
# orchestrator additionally runs it as shlex-quoted argv (never re-parsed by a shell).
TEST_CMD_RE = re.compile(r"[A-Za-z0-9 _./:=@%+,-]+")

batch = None
if SESSION_JOBS_ENABLED:
    config.load_incluster_config()
    batch = client.BatchV1Api()

_lock = threading.RLock()          # every status.json read-modify-write
_stop = threading.Event()          # stops the reconciler and the key revoker
_key_revocations: queue.Queue = queue.Queue()   # task ids whose LiteLLM key to delete now


# --------------------------------------------------------------------------- #
# Task state helpers
# --------------------------------------------------------------------------- #
class Conflict(Exception):
    """The task is already terminal: no more writes."""


def is_terminal(stage: object) -> bool:
    return isinstance(stage, str) and (stage in TERMINAL_STAGES or stage.startswith("error"))


def tdir(task_id: str, create: bool = False) -> Path:
    # task_id is always generated by us (16 hex); reject anything else so a crafted
    # id can never escape the data dir. Only create_task() creates the directory.
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise HTTPException(400, "bad task id")
    d = TASKS / task_id
    if create:
        d.mkdir(parents=True, exist_ok=False)
    return d


def _load(d: Path) -> dict | None:
    try:
        st = json.loads((d / "status.json").read_text())
        return st if isinstance(st, dict) else None
    except (OSError, ValueError):
        return None


def _store(d: Path, st: dict) -> None:
    tmp = d / "status.json.tmp"
    tmp.write_text(json.dumps(st))
    os.replace(tmp, d / "status.json")


def _revoke_local(d: Path) -> None:
    """Terminal: the task token stops working and the upload is no longer needed."""
    for name in ("token", "input.tgz", "input.tgz.tmp"):
        try:
            (d / name).unlink()
        except FileNotFoundError:
            pass


def update(tid: str, stage: str | None = None, *, only_from: set | None = None,
           meta_after_terminal: bool = False, **fields) -> dict:
    """Merge fields (and optionally a new stage) into status.json atomically.

    A stage change is refused (Conflict) once the task is terminal; so is any
    field write, unless meta_after_terminal (panel bookkeeping like downloaded_ts).
    only_from: change the stage only if the current stage is in this set.
    """
    d = tdir(tid)
    with _lock:
        cur = _load(d)
        if cur is None:
            if not d.is_dir():
                raise HTTPException(404, "no such task")
            cur = {}
        if is_terminal(cur.get("stage")) and (stage is not None or not meta_after_terminal):
            raise Conflict(tid)
        cur.update(fields)
        ended = False
        if stage is not None and (only_from is None or cur.get("stage") in only_from):
            now = time.time()
            cur.update(stage=stage, ts=now)
            if is_terminal(stage):
                cur["terminal_ts"] = now
                ended = True
        _store(d, cur)
        if is_terminal(cur.get("stage")):
            _revoke_local(d)
        if ended and cur.get("litellm_key_hash") and not cur.get("litellm_key_revoked"):
            _key_revocations.put(tid)   # the revoker thread deletes the task's LiteLLM key now
        if ended:
            audit("finish.task", cur, team=cur.get("team"), task_id=tid, stage=stage)
        return cur


def read_status(task_id: str) -> dict:
    st = _load(tdir(task_id))
    if st is None:
        raise HTTPException(404, "no such task")
    return st


def public_view(st: dict) -> dict:
    return {k: st[k] for k in PUBLIC_FIELDS if k in st}


def task_owner(task_id: str) -> str | None:
    try:
        return (tdir(task_id) / "owner").read_text().strip()
    except FileNotFoundError:
        return None


def active_tasks() -> int:
    n = 0
    try:
        dirs = list(TASKS.iterdir())
    except FileNotFoundError:
        return 0
    for d in dirs:
        if TASK_ID_RE.fullmatch(d.name):
            st = _load(d)
            if st is not None and not is_terminal(st.get("stage")):
                n += 1
    return n


def safe_rel(name: str | None) -> str:
    """Normalise an uploaded path to a safe POSIX-relative path (keeps directory
    structure, strips drive letters/absolute roots/'.'/'..' and control chars)."""
    name = "".join(ch for ch in (name or "file") if ch >= " ").replace("\\", "/")
    parts = [seg for seg in name.split("/") if seg not in ("", ".", "..")]
    if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
        parts = parts[1:]
    return "/".join(parts)[:1024] or "file"


def check_token(task_id: str, token: str | None) -> None:
    try:
        want = (tdir(task_id) / "token").read_text().strip()
    except FileNotFoundError:
        want = ""
    if not want or not token or not secrets.compare_digest(token.encode(), want.encode()):
        raise HTTPException(401, "bad task token")


def retention_rule() -> dict:
    return {"after_download_seconds": RETAIN_AFTER_DOWNLOAD_SECONDS,
            "after_terminal_seconds": RETAIN_AFTER_TERMINAL_SECONDS,
            "rule": (f"a task's data (incl. its bundle) is deleted {_human(RETAIN_AFTER_DOWNLOAD_SECONDS)} "
                     f"after the first bundle download or {_human(RETAIN_AFTER_TERMINAL_SECONDS)} "
                     "after the task ended, whichever comes first")}


def _tombstones(now: float | None = None) -> dict:
    """Task operation with directory authorization and bounded state."""
    now = time.time() if now is None else now
    try:
        raw = json.loads(TOMBSTONES.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if TASK_ID_RE.fullmatch(k) and isinstance(v, dict)
            and now - float(v.get("purged_ts") or 0) < TOMBSTONE_SECONDS}


def _add_tombstone(task_id: str, st: dict) -> None:
    now = time.time()
    with _lock:
        t = _tombstones(now)
        t[task_id] = {"owner": st.get("owner") or task_owner(task_id) or "", "team": st.get("team"), "purged_ts": now,
                      "stage": st.get("stage"), "terminal_ts": st.get("terminal_ts"),
                      "downloaded_ts": st.get("downloaded_ts")}
        tmp = TOMBSTONES.with_name(TOMBSTONES.name + ".tmp")
        tmp.write_text(json.dumps(t))
        os.replace(tmp, TOMBSTONES)


class TaskGone(Exception):
    """The caller's own task was purged by retention (-> 410 Gone)."""

    def __init__(self, task_id: str, tomb: dict):
        super().__init__(task_id)
        self.task_id, self.tomb = task_id, tomb


def audit(event: str, identity=None, team=None, task_id=None, outcome="allow", **detail) -> None:
    actor_user = identity.get("owner") if isinstance(identity, dict) else (
        identity.slug if identity else None)
    actor_sub = identity.get("actor_sub") if isinstance(identity, dict) else (
        identity.sub if identity else None)
    record = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "component": "panel", "event": event,
        "actor": {"user": actor_user, "sa": None, "sub": actor_sub},
        "team": team, "target": {"task_id": task_id} if task_id else {},
        "outcome": outcome, "detail": detail,
    }
    print(json.dumps(record, sort_keys=True), flush=True)


def owner_guard(task_id: str, request: Request, write=False, approve=False) -> None:
    identity = request.state.identity
    st = _load(tdir(task_id))
    if st is None:
        tomb = _tombstones().get(task_id)
        if tomb and identity.allows(tomb, write=write, approve=approve):
            raise TaskGone(task_id, tomb)
        raise HTTPException(404, "no such task")
    allowed = identity.allows(st, write=write, approve=approve)
    audit("authorize.task", identity, st.get("team"), task_id,
          "allow" if allowed else "deny", write=write, approve=approve)
    if not allowed:
        raise HTTPException(403, "task access denied")


def enforce_limits(user: str, team: str) -> None:
    active = [_load(d) for d in TASKS.glob("*") if TASK_ID_RE.fullmatch(d.name)]
    active = [st for st in active if st and not is_terminal(st.get("stage"))]
    if sum(st.get("owner") == user for st in active) >= MAX_ACTIVE_TASKS_PER_USER:
        raise HTTPException(429, "user active-task limit reached")
    if sum(st.get("team") == team for st in active) >= MAX_ACTIVE_TASKS_PER_TEAM:
        raise HTTPException(429, "team active-task limit reached")


def csrf_reason(h: Headers) -> str | None:
    """None if this state-changing request may proceed, else why not.

    Browsers send Origin on every POST (and Sec-Fetch-Site on all current
    engines), so a cross-site form/fetch always carries at least one of them
    and is refused unless it is same-origin with PANEL_ORIGIN. A request with
    NEITHER header cannot come from a browser page (it is e.g. the `aa` CLI,
    still token-authenticated), so it is not a CSRF vector and is allowed."""
    sfs, origin = h.get("sec-fetch-site"), h.get("origin")
    if sfs is not None and sfs.strip().lower() != "same-origin":
        return f"cross-site request refused (Sec-Fetch-Site: {sfs[:20]})"
    if origin is not None and _origin(origin) != PANEL_ORIGIN:
        return "cross-origin request refused (Origin)"
    return None


# --------------------------------------------------------------------------- #
# ASGI plumbing: gates, body limits, headers, port router
# --------------------------------------------------------------------------- #
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _reply(send, status: int, detail: str, headers: list | None = None) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"), *(headers or [])]})
    await send({"type": "http.response.body", "body": body})


async def _deny_non_http(scope, receive, send) -> bool:
    """No websockets anywhere: close them instead of letting them skip the gates."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return True
    return scope["type"] != "http"


class BodyLimit:
    """Cap request bodies per route BEFORE any parsing: reject on Content-Length,
    count streamed bytes for chunked bodies, and bound concurrent big uploads.
    On overflow we answer 413 ourselves and hand the app an http.disconnect
    (Starlette then aborts parsing); anything the app sends after that is
    dropped. uvicorn keeps draining the rest of the body, so the client still
    reads the 413."""

    def __init__(self, app, limit_for, heavy):
        self.app, self.limit_for, self.heavy = app, limit_for, heavy
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.limit_for(scope)
        cl = Headers(scope=scope).get("content-length")
        if cl is not None:
            if not cl.isdigit():
                return await _reply(send, 400, "bad Content-Length")
            if int(cl) > limit:
                return await _reply(send, 413, f"request body too large (> {limit} bytes)")
        heavy = self.heavy(scope)
        if heavy:
            if self.inflight >= MAX_CONCURRENT_UPLOADS:
                return await _reply(send, 429, "too many concurrent uploads; retry shortly")
            self.inflight += 1
        received, cut, started = 0, False, False

        async def recv():
            nonlocal received, cut
            if cut:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
                    cut = True
                    if not started:
                        await _reply(send, 413, f"request body too large (> {limit} bytes)")
                    return {"type": "http.disconnect"}
            return msg

        async def snd(msg):
            nonlocal started
            if cut:
                return
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        except Exception:  # noqa: BLE001
            if not cut:
                raise
        finally:
            if heavy:
                self.inflight -= 1


def _public_limit(scope) -> int:
    if scope["method"] == "POST" and scope["path"] == "/api/tasks":
        return MAX_UPLOAD_BYTES + 4 * MAX_BRIEF_CHARS + (1 << 20)  # + multipart overhead
    return 64 * 1024


def _public_heavy(scope) -> bool:
    return scope["method"] == "POST" and scope["path"] == "/api/tasks"


INTERNAL_PATH_RE = re.compile(r"/internal/tasks/([^/]+)/(input|status|bundle)")


def _internal_limit(scope) -> int:
    m = INTERNAL_PATH_RE.fullmatch(scope["path"])
    if scope["method"] == "POST" and m and m.group(2) == "bundle":
        return MAX_BUNDLE_BYTES
    if scope["method"] == "POST" and m and m.group(2) == "status":
        return MAX_STATUS_BODY
    return 16 * 1024


def _internal_heavy(scope) -> bool:
    return scope["method"] == "POST" and scope["path"].endswith("/bundle")


class SecurityHeaders:
    HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
               (b"x-frame-options", b"DENY"), (b"cross-origin-opener-policy", b"same-origin"),
               (b"cross-origin-resource-policy", b"same-origin")]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def snd(msg):
            if msg["type"] == "http.response.start":
                have = {k.lower() for k, _ in msg.get("headers", [])}
                extra = [(k, v) for k, v in self.HEADERS if k not in have]
                if b"cache-control" not in have:
                    extra.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": [*msg.get("headers", []), *extra]}
            await send(msg)
        await self.app(scope, receive, snd)


class PublicGate:
    """Access JWT on everything but /healthz; CSRF on state-changing requests;
    the active-task cap on uploads -- all before a single body byte is read."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if await _deny_non_http(scope, receive, send):
            return
        if scope["path"] == "/healthz":
            return await self.app(scope, receive, send)
        h = Headers(scope=scope)
        try:
            token = h.get("cf-access-jwt-assertion") if ACCESS_KIND == "cloudflare-access" else (
                h.get("authorization", "").removeprefix("Bearer ") or h.get("x-forwarded-access-token"))
            claims = await anyio.to_thread.run_sync(VERIFIER.verify, token)
            identity = await anyio.to_thread.run_sync(DIRECTORY.identify, claims)
            user = identity.slug
        except AccessDenied as exc:
            audit("authenticate.user", outcome="deny", reason=exc.reason)
            return await _reply(send, exc.status, exc.reason)
        scope.setdefault("state", {})["user"] = user
        scope["state"]["identity"] = identity
        if scope["path"].startswith("/api/tasks") and not SESSION_JOBS_ENABLED:
            return await _reply(send, 501, "session-jobs module is disabled; task dispatch is unavailable")
        if scope["method"] not in SAFE_METHODS and "auditor" in identity.roles:
            audit("authorize.task", identity, outcome="deny", reason="auditor read-only")
            return await _reply(send, 403, "auditor role is read-only")
        if scope["method"] not in SAFE_METHODS:
            why = csrf_reason(h)
            if why:
                log.warning("CSRF refused for %s: %s", user, why)
                return await _reply(send, 403, why)
        await self.app(scope, receive, send)


class InternalGate:
    """Per-task token + not-terminal check before the body is read."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if await _deny_non_http(scope, receive, send):
            return
        if scope["path"] == "/healthz":
            return await self.app(scope, receive, send)
        h = Headers(scope=scope)
        if any(name in h for name in ("cf-access-jwt-assertion", "cf-access-authenticated-user-email",
                                       "authorization", "x-forwarded-access-token")):
            return await _reply(send, 403, "internal endpoint not reachable publicly")
        m = INTERNAL_PATH_RE.fullmatch(scope["path"])
        if not m:
            return await _reply(send, 404, "Not Found")
        try:
            check_token(m.group(1), h.get("x-task-token"))
        except HTTPException as exc:
            return await _reply(send, exc.status_code, str(exc.detail))
        if scope["method"] == "POST":
            st = _load(tdir(m.group(1))) or {}
            if is_terminal(st.get("stage")):
                return await _reply(send, 409, "task is finished; no further writes")
        await self.app(scope, receive, send)


# --------------------------------------------------------------------------- #
# Public app (:8080, via the configured access proxy)
# --------------------------------------------------------------------------- #
public_app = FastAPI(title="Organisation panel", docs_url=None, redoc_url=None, openapi_url=None)
_create_lock = asyncio.Lock()
_streams: Counter = Counter()


def _human(seconds: int) -> str:
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= n and seconds % n == 0:
            k = seconds // n
            return f"{k} {unit}{'s' if k != 1 else ''}"
    return f"{seconds} seconds"


def _render_index() -> tuple[str, str]:
    html = (INDEX_HTML.read_text()
            .replace("{{RETAIN_AFTER_TERMINAL}}", _human(RETAIN_AFTER_TERMINAL_SECONDS))
            .replace("{{RETAIN_AFTER_DOWNLOAD}}", _human(RETAIN_AFTER_DOWNLOAD_SECONDS)))

    def hashes(tag: str) -> str:
        found = [base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode()
                 for m in re.finditer(rf"<{tag}>(.*?)</{tag}>", html, re.S)]
        return " ".join(f"'sha256-{x}'" for x in found) or "'none'"

    csp = (f"default-src 'none'; script-src {hashes('script')}; style-src {hashes('style')}; "
           "connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; "
           "base-uri 'none'")
    return html, csp


INDEX, INDEX_CSP = _render_index()


@public_app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX, headers={"Content-Security-Policy": INDEX_CSP})


@public_app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


def _file_size(f) -> int:
    f.seek(0, os.SEEK_END)
    n = f.tell()
    f.seek(0)
    return n


class _Capped(io.RawIOBase):
    """Read-through wrapper that raises once more than `limit` bytes come out
    (bounds the DECOMPRESSED stream: tar headers, pax records and data alike)."""

    def __init__(self, raw, limit: int):
        self.raw, self.limit, self.n = raw, limit, 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self.raw.read(len(b))
        self.n += len(data)
        if self.n > self.limit:
            raise HTTPException(413, f"archive expands beyond {self.limit} bytes "
                                     f"(cap {MAX_UNPACKED_BYTES} / ratio {MAX_ARCHIVE_RATIO}:1)")
        b[:len(data)] = data
        return len(data)


def _member_rel(name: str) -> str:
    """Strict path check for archive members: refuse, never repair."""
    bad = (not name or len(name) > 1024 or "\\" in name or name.startswith("/")
           or any(ord(c) < 32 or c == "\x7f" for c in name))
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if bad or not parts or ".." in parts or re.fullmatch(r"[A-Za-z]:", parts[0]):
        raise HTTPException(422, f"archive member {name[:200]!r}: absolute, '..' or invalid path")
    if any(p.lower() == ".git" for p in parts):
        raise HTTPException(422, f"archive member {name[:200]!r}: .git content is not accepted")
    return "/".join(parts)


def _write_input(d: Path, brief: str, files: list[UploadFile],
                 archive: UploadFile | None, task_meta: dict | None = None) -> tuple[int, int]:
    """input.tgz = {brief.txt, task.json, files/...}, streamed from the spooled
    upload(s) to disk. task.json is the validated, non-secret task metadata
    (task_id, mode, base_commit, repos, has_test_cmd) for the orchestrator's
    prompts. Returns (file count, uncompressed bytes)."""
    tmp = d / "input.tgz.tmp"
    now = int(time.time())
    count = total = 0
    seen: set[str] = set()
    with tarfile.open(tmp, mode="w:gz") as out:
        for name, data in (("brief.txt", brief.encode()),
                           ("task.json", json.dumps(task_meta or {}, sort_keys=True).encode())):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(data), now, 0o644
            out.addfile(ti, io.BytesIO(data))
        for f in files:                       # multipart parts: content only, mode 0644
            rel = safe_rel(f.filename)
            if any(p.lower() == ".git" for p in rel.split("/")):
                log.warning("dropped uploaded .git path %r", rel[:200])
                continue
            ti = tarfile.TarInfo(f"files/{rel}")
            ti.size, ti.mtime, ti.mode = _file_size(f.file), now, 0o644
            out.addfile(ti, f.file)
            count, total = count + 1, total + ti.size
        if archive is not None:
            comp = _file_size(archive.file)
            if archive.file.read(2) != b"\x1f\x8b":
                raise HTTPException(422, "archive must be a gzip-compressed tar")
            archive.file.seek(0)
            limit = min(MAX_UNPACKED_BYTES, max(MAX_ARCHIVE_RATIO * comp, 8 << 20))
            try:
                with gzip.GzipFile(fileobj=archive.file, mode="rb") as gz, \
                        tarfile.open(fileobj=_Capped(gz, limit), mode="r|") as src:
                    for m in src:
                        if m.isdir():
                            continue
                        if not m.isreg() or m.issparse():
                            raise HTTPException(422, f"archive member {m.name[:200]!r}: only regular "
                                                     "files are accepted (no links, devices, fifos)")
                        rel = _member_rel(m.name)
                        if rel in seen:
                            raise HTTPException(422, f"archive member {rel[:200]!r} appears twice")
                        seen.add(rel)
                        count, total = count + 1, total + m.size
                        if count > MAX_UPLOAD_FILES:
                            raise HTTPException(413, f"too many files (> {MAX_UPLOAD_FILES})")
                        ti = tarfile.TarInfo(f"files/{rel}")
                        # keep only the executable bit, so the patch keeps 100755
                        ti.size, ti.mtime = m.size, now
                        ti.mode = 0o755 if m.mode & 0o111 else 0o644
                        out.addfile(ti, src.extractfile(m))
            except (tarfile.TarError, OSError, EOFError, zlib_error) as exc:
                raise HTTPException(422, f"archive is not a valid tar.gz ({type(exc).__name__})") from None
    if count > MAX_UPLOAD_FILES:
        raise HTTPException(413, f"too many files (> {MAX_UPLOAD_FILES})")
    os.replace(tmp, d / "input.tgz")
    return count, total


def _meta(base_commit: str | None, repos: list[str], test_cmd: str | None) -> dict:
    """Validate the optional client metadata (stored + returned in status)."""
    meta: dict = {}
    if base_commit:
        bc = base_commit.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{7,64}", bc):
            raise HTTPException(422, "base_commit must be a 7-64 char hex commit id")
        meta["base_commit"] = bc
    if len(repos) == 1 and repos[0].strip().startswith("["):
        try:
            repos = json.loads(repos[0])
        except ValueError:
            raise HTTPException(422, "repos must be a JSON list or repeated fields") from None
    if repos:
        if not isinstance(repos, list) or len(repos) > 32 or not all(
                isinstance(r, str) and 0 < len(r.strip()) <= 200
                and all(32 <= ord(c) != 127 for c in r) for r in repos):
            raise HTTPException(422, "repos: up to 32 printable names, 200 chars each")
        meta["repos"] = [r.strip() for r in repos]
    if test_cmd is not None and test_cmd.strip():
        tc = test_cmd.strip()
        if len(tc) > MAX_TEST_CMD_CHARS or not TEST_CMD_RE.fullmatch(tc) or not shlex.split(tc):
            raise HTTPException(422, "test_cmd: one line, <= 300 chars of [A-Za-z0-9 _./:=@%+,-] "
                                     "(no shell syntax; it runs as a plain argv)")
        meta["test_cmd"] = tc
    return meta


def _mode(mode: str | None) -> str:
    """Validate the optional task mode: standard (default) | bulk. Each mode is a
    team budget class for the task's own key."""
    m = (mode or "").strip().lower() or "standard"
    if m not in TASK_MODES:
        raise HTTPException(422, f"mode must be one of: {', '.join(TASK_MODES)}")
    return m


@public_app.post("/api/tasks")
async def create_task(request: Request, brief: str = Form(...),
                      files: list[UploadFile] = File(default=[]),
                      archive: UploadFile | None = File(default=None),
                      base_commit: str | None = Form(default=None),
                      repos: list[str] = Form(default=[]),
                      test_cmd: str | None = Form(default=None),
                      mode: str | None = Form(default=None),
                      team: str | None = Form(default=None),
                      account_id: str | None = Form(default=None),
                      data_class: str = Form(default="internal")) -> JSONResponse:
    """Task operation with directory authorization and bounded state."""
    user = request.state.user
    identity = request.state.identity
    if not SESSION_JOBS_ENABLED:
        raise HTTPException(501, "session-jobs module is disabled")
    if "auditor" in identity.roles:
        raise HTTPException(403, "auditor role is read-only")
    directory_user = next(u for u in DIRECTORY.rows("users") if u["slug"] == user)
    team = team or directory_user.get("primary_team")
    if team not in identity.teams:
        raise HTTPException(403, "task team requires directory and token membership")
    team_row = DIRECTORY.team(team)
    if data_class not in team_row.get("data_classes_allowed", []):
        raise HTTPException(403, "data class is not permitted")
    accounts = [a for a in DIRECTORY.rows("accounts") if a["id"] in team_row.get("pools", [])
                and a.get("type") in ("api", "pool")
                and (a.get("owner_team") == team or team in a.get("shared_with", [])
                     or "*" in a.get("shared_with", []))
                and a.get("vendor") in team_row.get("vendors_allowed", {}).get(data_class, [])]
    account = next((a for a in accounts if a["id"] == account_id), None) if account_id else (
        next((a for a in accounts if a.get("vendor") == "local"), None))
    if account is None:
        raise HTTPException(403, "no entitled task account")
    account_id = account["id"]
    # Browsers submit form text with CRLF; store LF so brief.txt (and the
    # RUNBOOK.md quote built from it on Linux) carries no stray CRs.
    brief = (brief or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not brief:
        raise HTTPException(422, "brief is required")
    if len(brief) > MAX_BRIEF_CHARS:
        raise HTTPException(413, f"brief too long (> {MAX_BRIEF_CHARS} chars)")
    files = files or []
    if files and archive is not None:
        raise HTTPException(422, "send either files or archive, not both")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(413, f"too many files (> {MAX_UPLOAD_FILES})")
    meta = _meta(base_commit, repos or [], test_cmd)
    meta["mode"] = _mode(mode)
    meta.update(team=team, account_id=account_id, data_class=data_class)
    sent = 0  # compressed bytes for an archive, raw bytes for parts
    for f in files + ([archive] if archive is not None else []):
        sent += await anyio.to_thread.run_sync(_file_size, f.file)
    if sent > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload too large (> {MAX_UPLOAD_BYTES} bytes)")

    async with _create_lock:  # the active-task cap is checked + claimed atomically
        enforce_limits(user, team)
        task_id, token = secrets.token_hex(8), secrets.token_hex(24)
        d = tdir(task_id, create=True)
        try:
            fd = os.open(d / "token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(token)
            (d / "owner").write_text(user)
            task_meta = {"task_id": task_id, "mode": meta["mode"],
                         "user": user, "team": team, "account_id": account_id,
                         "data_class": data_class,
                         "has_test_cmd": "test_cmd" in meta,
                         **{k: meta[k] for k in ("base_commit", "repos") if k in meta}}
            count, total = await anyio.to_thread.run_sync(_write_input, d, brief, files, archive,
                                                          task_meta)
            update(task_id, "queued", task_id=task_id, owner=user, created_ts=time.time(),
                   actor_sub=identity.sub,
                   title=brief.splitlines()[0][:120], **meta)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
    audit("create.task", identity, team, task_id)

    try:
        await anyio.to_thread.run_sync(_dispatch, task_id, token, meta.get("test_cmd"),
                                       meta["mode"])
    except Exception as exc:  # noqa: BLE001 -- surface any dispatch failure to the UI
        if isinstance(exc, KeyMintError):   # fail closed: no per-task key, no Job
            log.error("dispatch refused for task %s: %s", task_id, exc)
            detail = "could not mint the task's own LiteLLM key; nothing was started"
        else:
            audit("dispatch.task", identity, team, task_id, "error", reason="job creation failed")
            detail = "could not create the session Job"
        try:
            update(task_id, "error-dispatch", detail=detail)
        except Conflict:
            pass
        # task_id included so a client can inspect (status) or clean up (cancel).
        return JSONResponse({"detail": "dispatch failed; see panel logs", "task_id": task_id},
                            status_code=502)
    return JSONResponse({"task_id": task_id})


@public_app.get("/api/meta")
async def api_meta() -> dict:
    """What this panel supports, for clients: API version, features, limits."""
    features = ["archive", "list", "cancel", "metadata", "mode", "task-context", "whoami",
                "gone-410", "bundle-409-reason", "bulk", "per-task-keys"]
    return {
        "api_version": API_VERSION, "panel_build": PANEL_BUILD, "features": features,
        "session_jobs_enabled": SESSION_JOBS_ENABLED,
        "modes": list(TASK_MODES),
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES, "max_upload_files": MAX_UPLOAD_FILES,
            "max_unpacked_bytes": MAX_UNPACKED_BYTES, "max_archive_ratio": MAX_ARCHIVE_RATIO,
            "max_brief_chars": MAX_BRIEF_CHARS, "max_test_cmd_chars": MAX_TEST_CMD_CHARS,
            "max_repos": 32, "max_bundle_bytes": MAX_BUNDLE_BYTES,
            "max_active_tasks_per_user": MAX_ACTIVE_TASKS_PER_USER,
            "max_active_tasks_per_team": MAX_ACTIVE_TASKS_PER_TEAM,
            "retain_after_download_seconds": RETAIN_AFTER_DOWNLOAD_SECONDS,
            "retain_after_terminal_seconds": RETAIN_AFTER_TERMINAL_SECONDS,
            "tombstone_seconds": TOMBSTONE_SECONDS,
        },
    }


@public_app.get("/api/whoami")
@public_app.get("/api/me")
async def whoami(request: Request) -> dict:
    identity = request.state.identity
    return {"slug": identity.slug, "identity": identity.slug, "kind": "user",
            "teams": list(identity.teams), "roles": list(identity.roles)}


@public_app.get("/api/tasks")
async def list_tasks(request: Request, limit: int = Query(20, ge=1, le=100),
                     offset: int = Query(0, ge=0)) -> dict:
    """The caller's own tasks, newest first: {tasks: [...], next_offset}."""
    user = request.state.user
    mine = []
    try:
        dirs = list(TASKS.iterdir())
    except FileNotFoundError:
        dirs = []
    for d in dirs:
        if TASK_ID_RE.fullmatch(d.name):
            st = _load(d)
            if st and request.state.identity.allows(st):
                stage = st.get("stage")
                mine.append({"task_id": d.name, "created_ts": st.get("created_ts"),
                             "stage": stage, "ts": st.get("ts"),
                             "title": st.get("title", ""), "verified": st.get("verified"),
                             "mode": st.get("mode", "standard"), "repos": st.get("repos", []),
                             "base_commit": st.get("base_commit"),
                             "owner": st.get("owner"), "team": st.get("team"),
                             "approved_by": st.get("approved_by"),
                             "cancelled": bool(st.get("cancelled")),
                             "terminal": is_terminal(stage)})
    mine.sort(key=lambda t: (t["created_ts"] or 0, t["task_id"]), reverse=True)
    page = mine[offset:offset + limit]
    return {"tasks": page, "next_offset": offset + limit if offset + limit < len(mine) else None}


@public_app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict:
    """Task operation with directory authorization and bounded state."""
    owner_guard(task_id, request, write=True)
    st = read_status(task_id)
    if is_terminal(st.get("stage")):
        raise HTTPException(409, "task already finished")
    problems = await anyio.to_thread.run_sync(_teardown, task_id, st["job"]) if st.get("job") else []
    try:
        st = update(task_id, "error-cancelled", cancelled=True,
                    detail=f"cancelled by {request.state.user}")
    except Conflict:
        raise HTTPException(409, "task already finished") from None
    audit("cancel.task", request.state.identity, st.get("team"), task_id)
    out = {"ok": True, "task_id": task_id, "stage": st["stage"]}
    if problems:
        out["warning"] = "cancelled, but cleanup was incomplete: " + "; ".join(problems)
    return out


@public_app.get("/api/tasks/{task_id}/status")
@public_app.get("/api/tasks/{task_id}")
async def status(task_id: str, request: Request) -> dict:
    owner_guard(task_id, request)
    return public_view(read_status(task_id))


@public_app.post("/api/tasks/{task_id}/approve")
async def approve_task(task_id: str, request: Request) -> dict:
    owner_guard(task_id, request, write=True, approve=True)
    with _lock:
        st = read_status(task_id)
        if st.get("stage") != "needs-review":
            raise HTTPException(409, "only a needs-review bundle can be approved")
        if st.get("approved_by"):
            raise HTTPException(409, "bundle is already approved")
        update(task_id, meta_after_terminal=True, approved_by=request.state.user,
               approved_ts=time.time())
    audit("approve.task", request.state.identity, st.get("team"), task_id)
    return {"ok": True, "task_id": task_id, "approved_by": request.state.user}


@public_app.get("/api/tasks/{task_id}/events")
async def events(task_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: emit each status change and a heartbeat, until the
    task is terminal. An async generator, so an open stream holds no worker
    thread (the reconciler, not this stream, watches the Job)."""
    owner_guard(task_id, request)
    if _streams[task_id] >= MAX_STREAMS_PER_TASK or sum(_streams.values()) >= MAX_STREAMS:
        raise HTTPException(429, "too many open event streams")

    async def gen():
        _streams[task_id] += 1
        try:
            last = None
            deadline = time.monotonic() + EVENT_DEADLINE_SECONDS + 60
            while True:
                st = _load(tdir(task_id))
                if st is None:
                    yield f"data: {json.dumps({'stage': 'error-gone', 'detail': 'task data was deleted'})}\n\n"
                    break
                view = public_view(st)
                if view != last:
                    yield f"data: {json.dumps(view)}\n\n"
                    last = view
                if is_terminal(view.get("stage")) or time.monotonic() >= deadline:
                    break
                yield ": ping\n\n"
                await asyncio.sleep(1)
        finally:
            _streams[task_id] -= 1
            if _streams[task_id] <= 0:
                del _streams[task_id]

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@public_app.get("/api/tasks/{task_id}/bundle")
async def download(task_id: str, request: Request) -> Response:
    """bundle.tgz, or 409 {detail, reason: not-ready | failed, stage}."""
    owner_guard(task_id, request)
    p = tdir(task_id) / "bundle.tgz"
    st = read_status(task_id)
    if not p.exists():
        stage = st.get("stage")
        if isinstance(stage, str) and stage.startswith("error"):
            return JSONResponse({"detail": f"task failed ({stage}); it has no bundle",
                                 "reason": "failed", "stage": stage}, status_code=409)
        return JSONResponse({"detail": "bundle not ready", "reason": "not-ready", "stage": stage},
                            status_code=409)
    if not st.get("downloaded_ts"):
        update(task_id, meta_after_terminal=True, downloaded_ts=time.time())
    audit("download.task", request.state.identity, st.get("team"), task_id)
    return FileResponse(p, media_type="application/gzip", filename=f"agent-array-{task_id}.tgz")


# --------------------------------------------------------------------------- #
# Internal app (:8081, session pods only; per-task token)
# --------------------------------------------------------------------------- #
internal_app = FastAPI(title="Panel task callbacks", docs_url=None, redoc_url=None,
                       openapi_url=None)


@internal_app.get("/healthz", response_class=PlainTextResponse)
async def internal_healthz() -> str:
    return "ok"


def _drop_input(task_id: str) -> None:
    try:
        (tdir(task_id) / "input.tgz").unlink()
    except FileNotFoundError:
        pass


@internal_app.get("/internal/tasks/{task_id}/input")
async def internal_input(task_id: str, request: Request) -> FileResponse:
    check_token(task_id, request.headers.get("x-task-token"))
    p = tdir(task_id) / "input.tgz"
    if not p.exists():
        raise HTTPException(410, "input already fetched")
    # One fetch per task (backoffLimit 0): drop the upload as soon as it is sent.
    return FileResponse(p, media_type="application/gzip",
                        background=BackgroundTask(_drop_input, task_id))


@internal_app.post("/internal/tasks/{task_id}/status")
async def internal_status(task_id: str, request: Request) -> dict:
    check_token(task_id, request.headers.get("x-task-token"))
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    stage = body.get("stage")
    if not isinstance(stage, str) or not (stage in JOB_STAGES or ERROR_STAGE_RE.fullmatch(stage)):
        raise HTTPException(422, "unknown stage")
    extra = {}
    if isinstance(body.get("detail"), str):
        extra["detail"] = body["detail"][:MAX_DETAIL_CHARS]
    pct = body.get("pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100:
        extra["pct"] = pct
    try:
        update(task_id, stage, **extra)
    except Conflict:
        raise HTTPException(409, "task is finished; no further writes") from None
    return {"ok": True}


@internal_app.post("/internal/tasks/{task_id}/bundle")
async def internal_bundle(task_id: str, request: Request) -> dict:
    """Raw application/gzip body; X-Bundle-Verified: true|false. Streamed to disk
    under the cap; accepted at most once, and never after a terminal stage."""
    check_token(task_id, request.headers.get("x-task-token"))
    verified = request.headers.get("x-bundle-verified", "false").strip().lower() == "true"
    d = tdir(task_id)
    part = d / "bundle.tgz.part"
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise HTTPException(409, "a bundle upload is already in progress") from None
    try:
        total, head = 0, b""
        with os.fdopen(fd, "wb") as out:
            try:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_BUNDLE_BYTES:
                        raise HTTPException(413, f"bundle too large (> {MAX_BUNDLE_BYTES} bytes)")
                    if len(head) < 2:
                        head += chunk[:2 - len(head)]
                    out.write(chunk)
            except ClientDisconnect:
                raise HTTPException(400, "bundle upload aborted") from None
        if head != b"\x1f\x8b":
            raise HTTPException(422, "bundle is not a gzip archive")
        with _lock:
            st = _load(d) or {}
            if is_terminal(st.get("stage")) or (d / "bundle.tgz").exists():
                raise HTTPException(409, "task is finished; no further writes")
            os.replace(part, d / "bundle.tgz")
            update(task_id, "done" if verified else "needs-review", verified=verified)
    finally:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
    log.info("task %s bundle received (%d bytes, verified=%s)", task_id, total, verified)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Dispatch + reconciliation (k8s client is blocking: called from threads)
# --------------------------------------------------------------------------- #
def _set_env(container: dict, name: str, value: str) -> None:
    """Replace-or-append one literal env var on a parsed container."""
    env = [e for e in container.get("env") or [] if e.get("name") != name]
    container["env"] = env + [{"name": name, "value": value}]


def _render_job(task_id: str, test_cmd: str | None = None, mode: str = "standard") -> dict:
    raw = TEMPLATE.read_text().replace("__TASK_ID__", task_id).replace("__IMAGE__", SESSION_IMAGE)
    manifest = yaml.safe_load(raw)
    spec = manifest["spec"]["template"]["spec"]
    (session,) = [c for c in spec["containers"] if c.get("name") == "session"]
    if test_cmd:
        _set_env(session, "TEST_CMD", test_cmd)
    st = read_status(task_id)
    labels = {f"{LABEL_PREFIX}/{name}": str(value) for name, value in (
        ("user", st["owner"]), ("team", st["team"]),
        ("account", st["account_id"]), ("task", task_id))}
    labels[f"{LABEL_PREFIX}/llm-client"] = "true"
    manifest["metadata"].setdefault("labels", {}).update(labels)
    manifest["spec"]["template"]["metadata"].setdefault("labels", {}).update(labels)
    if spec.get("runtimeClassName") != SESSION_RUNTIME_CLASS or spec.get("automountServiceAccountToken") is not False:
        raise RuntimeError("job template lost runtime isolation or disables no API token")
    return manifest


# --- per-task LiteLLM keys (see "Per-task LiteLLM virtual keys" above) ------- #
class KeyMintError(RuntimeError):
    """No usable per-task key: the task must not be dispatched (fail closed)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_kw):
        return None   # never re-send the minting key somewhere else


# No proxies from the environment and no redirects: the minting key goes to
# LITELLM_ADMIN_BASE and nowhere else.
_ADMIN_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
_SK_RE = re.compile(r"sk-[A-Za-z0-9_.\-]+")


_NET_ERRORS = (OSError, http.client.HTTPException)   # URLError, timeouts, resets, bad replies


def _admin_post(path: str, body: dict) -> tuple[int, dict]:
    """POST JSON to LiteLLM's admin API with the minting key. Returns (status,
    JSON object or {}). Network failures raise one of _NET_ERRORS."""
    req = urllib.request.Request(
        LITELLM_ADMIN_BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {LITELLM_MINT_KEY}",
                 "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with _ADMIN_OPENER.open(req, timeout=LITELLM_ADMIN_TIMEOUT_SECONDS) as r:
            status, raw = r.status, r.read(1 << 20)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(1 << 16)
        exc.close()
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = {}
    return status, doc if isinstance(doc, dict) else {}


def _admin_error(doc: dict) -> str:
    """LiteLLM's error text, short and with anything key-shaped redacted."""
    err = doc.get("error")
    msg = err.get("message") if isinstance(err, dict) else err or doc.get("detail") or ""
    return _SK_RE.sub("sk-<redacted>", str(msg))[:200]


def _task_key_request(task_id: str, mode: str) -> dict:
    st = read_status(task_id)
    team = DIRECTORY.team(st["team"])
    account = next(a for a in DIRECTORY.rows("accounts") if a["id"] == st["account_id"])
    models = sorted(set(team["litellm"]["models"]) & set(TASK_ALLOWED_MODELS)
                    & set(account.get("litellm_models", [])))
    budget = team["litellm"]["task_budget_usd"].get(mode)
    if (not models or not isinstance(budget, (int, float)) or isinstance(budget, bool)
            or not math.isfinite(budget) or budget <= 0):
        raise KeyMintError("team task models or budget unavailable")
    return {
        "key_alias": f"panel-{task_id}", "team_id": st["team"], "user_id": st["owner"],
        "key_type": "llm_api", "models": models, "max_budget": budget,
        "duration": f"{TASK_KEY_TTL_SECONDS}s",
        "metadata": {"task_id": task_id, "account_id": st["account_id"],
                     "data_class": st["data_class"], "client": "panel"},
    }


def _expires_in(value: object) -> float | None:
    """Seconds from now until LiteLLM's `expires` (ISO 8601), None if unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp() - time.time()


def _mint_task_key(task_id: str, mode: str) -> tuple[str, str]:
    """Mint the task's own LiteLLM key: (key, sha256 hex of it). Raises
    KeyMintError on ANY doubt, after deleting a key that came back wrong."""
    want = _task_key_request(task_id, mode)
    try:
        status, doc = _admin_post("/key/generate", want)
    except _NET_ERRORS as exc:
        # LiteLLM may have minted it before the connection broke: delete by alias.
        raise KeyMintError(f"LiteLLM admin API unreachable ({type(exc).__name__})"
                           + _cleanup_note(_delete_task_key(task_id, None))) from None
    if status != 200:
        raise KeyMintError(f"LiteLLM /key/generate answered {status}")
    key = doc.get("key")
    if not isinstance(key, str) or not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,256}", key):
        raise KeyMintError("LiteLLM /key/generate returned no usable key"
                           + _cleanup_note(_delete_task_key(task_id, None)))
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    # Defence in depth: the stored key must be exactly what was asked for (an
    # empty models list would mean ALL models; no expiry would mean forever).
    left = _expires_in(doc.get("expires"))
    wrong = [name for name, ok in (
        ("key_alias", doc.get("key_alias") == want["key_alias"]),
        ("user_id", doc.get("user_id") == want["user_id"]),
        ("team_id", doc.get("team_id") == want["team_id"]),
        ("models", isinstance(doc.get("models"), list) and bool(doc["models"])
         and sorted(doc["models"]) == sorted(want["models"])),
        ("max_budget", isinstance(doc.get("max_budget"), (int, float))
         and abs(doc["max_budget"] - want["max_budget"]) < 1e-9),
        ("allowed_routes", doc.get("allowed_routes") == ["llm_api_routes"]),
        ("expires", left is not None and left > 0
         and abs(left - TASK_KEY_TTL_SECONDS) <= min(300, TASK_KEY_TTL_SECONDS / 10)),
        ("metadata.task_id", (doc.get("metadata") or {}).get("task_id") == task_id),
        ("metadata", doc.get("metadata") == want["metadata"]),
        ("token", doc.get("token") in (None, key_hash)),
    ) if not ok]
    if wrong:
        raise KeyMintError("LiteLLM stored a key unlike the request (" + ", ".join(wrong) + ")"
                           + _cleanup_note(_delete_task_key(task_id, key_hash)))
    audit("mint.key", read_status(task_id), team=want["team_id"], task_id=task_id)
    return key, key_hash


def _cleanup_note(problem: str | None) -> str:
    return f"; a key it may have stored is NOT deleted: {problem}" if problem else ""


def _delete_task_key(task_id: str, key_hash: str | None) -> str | None:
    """POST /key/delete for the task's key, by its sha256 (or its alias when the
    hash is unknown). None = the key is gone (deleted now, or LiteLLM has no such
    key); otherwise why it may still exist."""
    body = {"keys": [key_hash]} if key_hash else {"key_aliases": [f"panel-{task_id}"]}
    try:
        status, doc = _admin_post("/key/delete", body)
    except _NET_ERRORS as exc:
        return f"LiteLLM key not deleted ({type(exc).__name__})"
    if status == 200:
        return None
    # v1.104 answers a missing key with 404 {"error": {"message": "...No keys found..."}};
    # any other 404 (e.g. a wrong LITELLM_ADMIN_BASE path) is NOT proof it is gone.
    if status == 404 and "no keys found" in _admin_error(doc).lower():
        return None
    return f"LiteLLM key not deleted ({status})"


def _revoke_task_key(task_id: str) -> str | None:
    """Delete the task's minted key unless already done, and record it. Returns
    a problem string while the key may still exist (retried by the reconciler)."""
    st = _load(tdir(task_id)) or {}
    key_hash = st.get("litellm_key_hash")
    if not key_hash or st.get("litellm_key_revoked"):
        return None
    problem = _delete_task_key(task_id, key_hash)
    if problem:
        log.error("task %s: %s (retried by the reconciler; the key expires on its own "
                  "%ss after dispatch)", task_id, problem, TASK_KEY_TTL_SECONDS)
        return problem
    try:
        update(task_id, meta_after_terminal=True, litellm_key_revoked=True,
               litellm_key_revoked_ts=time.time())
    except (Conflict, HTTPException):
        pass
    log.info("task %s: LiteLLM key deleted", task_id)
    audit("revoke.key", st, team=st.get("team"), task_id=task_id)
    return None


def _key_revoker() -> None:
    """Deletes a task's key as soon as update() sees the task end."""
    while not _stop.is_set():
        try:
            task_id = _key_revocations.get(timeout=1)
        except queue.Empty:
            continue
        try:
            _revoke_task_key(task_id)
        except Exception:  # noqa: BLE001 -- never let the thread die; the reconciler retries
            log.exception("key revocation failed for task %s", task_id)


def _teardown(task_id: str, job_name: str) -> list[str]:
    """Task operation with directory authorization and bounded state."""
    problems = []
    err = _delete_job(job_name)
    if err:
        problems.append(err)
    try:
        err = _revoke_task_key(task_id)
    except Exception as exc:  # noqa: BLE001
        err = f"LiteLLM key not deleted ({type(exc).__name__})"
    if err:
        problems.append(err)
    return problems


def _delete_job(name: str) -> str | None:
    """Delete a Job; None when it is gone (deleted now or already), else why not."""
    try:
        batch.delete_namespaced_job(name, NS, propagation_policy="Background")
    except client.exceptions.ApiException as exc:
        if exc.status != 404:
            log.error("could not delete Job %s/%s (HTTP %s)", NS, name, exc.status)
            return f"Job {name} not deleted ({exc.status})"
    except Exception as exc:  # noqa: BLE001
        log.error("could not delete Job %s/%s (%s)", NS, name, type(exc).__name__)
        return f"Job {name} not deleted ({type(exc).__name__})"
    return None


def _dispatch(task_id: str, token: str, test_cmd: str | None = None,
              mode: str = "standard") -> None:
    if not SESSION_JOBS_ENABLED:
        raise RuntimeError("session-jobs module is disabled")
    manifest = _render_job(task_id, test_cmd, mode)
    key, key_hash = _mint_task_key(task_id, mode)
    session = next(c for c in manifest["spec"]["template"]["spec"]["containers"] if c["name"] == "session")
    _set_env(session, "TASK_TOKEN", token)
    _set_env(session, "LITELLM_KEY", key)
    _set_env(session, "TASK_MODELS", ",".join(_task_key_request(task_id, mode)["models"]))
    name = manifest["metadata"]["name"]
    try:
        update(task_id, litellm_key_hash=key_hash, litellm_key_alias=f"panel-{task_id}",
               litellm_key_expires_ts=time.time() + TASK_KEY_TTL_SECONDS, job=name)
        batch.create_namespaced_job(NS, manifest)
        update(task_id, "dispatched", only_from={"queued"}, dispatched_ts=time.time())
        st = read_status(task_id)
        audit("dispatch.task", st, team=st["team"], task_id=task_id)
    except BaseException:
        _delete_job(name)
        _delete_task_key(task_id, key_hash)
        raise


def _job_failure(job_name: str) -> str | None:
    """Return a human reason if the Job has failed or vanished, else None."""
    try:
        st = batch.read_namespaced_job(job_name, NS).status
    except client.exceptions.ApiException as exc:
        if exc.status == 404:
            return "session job no longer exists"
        if exc.status in (401, 403):
            log.error("cannot read Job %s status (%s): check panel RBAC (jobs get)",
                      job_name, exc.status)
        return None  # transient API error: don't false-alarm
    if st and (st.failed or 0) >= 1:
        for c in (st.conditions or []):
            if c.type == "Failed" and c.status == "True":
                return c.reason or c.message or "session job failed"
        return "session job failed"
    return None


def _finish(task_id: str, stage: str, detail: str) -> bool:
    try:
        update(task_id, stage, detail=detail)
        return True
    except (Conflict, HTTPException):
        return False


def reconcile_once() -> None:
    """Task operation with directory authorization and bounded state."""
    now = time.time()
    try:
        dirs = sorted(TASKS.iterdir())
    except FileNotFoundError:
        return
    for d in dirs:
        if not TASK_ID_RE.fullmatch(d.name):
            continue
        task_id, st = d.name, _load(d)
        if st is None:
            try:
                if now - d.stat().st_mtime > ORPHAN_DIR_SECONDS:
                    shutil.rmtree(d, ignore_errors=True)
            except FileNotFoundError:
                pass
            continue
        stage = st.get("stage")
        if is_terminal(stage):
            # The task's LiteLLM key: deleted at the end (revoker thread); retried here
            # every pass until LiteLLM confirms it (its expiry is the backstop).
            if st.get("litellm_key_hash") and not st.get("litellm_key_revoked"):
                try:
                    _revoke_task_key(task_id)
                except Exception:  # noqa: BLE001 -- one task must not stop the pass
                    log.exception("key revocation failed for task %s", task_id)
            tts = st.get("terminal_ts") or st.get("ts") or now
            dts = st.get("downloaded_ts")
            if now - tts >= RETAIN_AFTER_TERMINAL_SECONDS or \
                    (dts and now - dts >= RETAIN_AFTER_DOWNLOAD_SECONDS):
                with _lock:
                    try:
                        _add_tombstone(task_id, st)
                    except OSError as exc:      # never block retention on the index
                        log.error("could not record tombstone for %s: %s", task_id, exc)
                    shutil.rmtree(d, ignore_errors=True)
                log.info("task %s purged (retention)", task_id)
                continue
            _revoke_local(d)
            if st.get("job"):
                _delete_job(st["job"])
            continue
        started = st.get("dispatched_ts") or st.get("created_ts") or st.get("ts") or now
        if now - started >= EVENT_DEADLINE_SECONDS:
            if _finish(task_id, "error-timeout", "no terminal status before the deadline") \
                    and st.get("job"):
                _teardown(task_id, st["job"])
        elif st.get("job"):
            verdict = _job_failure(st["job"])
            if verdict:
                _finish(task_id, "error-job", verdict)


def _reconciler() -> None:
    while True:
        try:
            reconcile_once()
        except Exception:  # noqa: BLE001 -- never let the loop die
            log.exception("reconcile pass failed")
        if _stop.wait(RECONCILE_INTERVAL_SECONDS):
            return


# --------------------------------------------------------------------------- #
# Port router + entrypoint
# --------------------------------------------------------------------------- #
@public_app.exception_handler(TaskGone)
async def _task_gone(request: Request, exc: TaskGone) -> JSONResponse:
    t = exc.tomb
    return JSONResponse({"detail": f"task {exc.task_id} was purged by the retention policy",
                         "reason": "purged", "task_id": exc.task_id,
                         "purged_ts": t.get("purged_ts"), "stage": t.get("stage"),
                         "retention": retention_rule()}, status_code=410)


PUBLIC_ASGI = SecurityHeaders(PublicGate(BodyLimit(public_app, _public_limit, _public_heavy)))
INTERNAL_ASGI = InternalGate(BodyLimit(internal_app, _internal_limit, _internal_heavy))


async def _lifespan(receive, send) -> None:
    while True:
        msg = await receive()
        if msg["type"] == "lifespan.startup":
            TASKS.mkdir(parents=True, exist_ok=True)
            _stop.clear()
            workers = [threading.Thread(target=_reconciler, name="reconciler"),
                       threading.Thread(target=_key_revoker, name="key-revoker")]
            for worker in workers:
                worker.start()
            await send({"type": "lifespan.startup.complete"})
        elif msg["type"] == "lifespan.shutdown":
            _stop.set()
            for worker in workers:
                await anyio.to_thread.run_sync(worker.join)
            await send({"type": "lifespan.shutdown.complete"})
            return


async def app(scope, receive, send):
    """Dispatch on the LOCAL port the connection arrived on (socket-level, not a
    header): :PUBLIC_PORT -> public app, :INTERNAL_PORT -> internal app."""
    if scope["type"] == "lifespan":
        return await _lifespan(receive, send)
    port = (scope.get("server") or (None, None))[1]
    if port == PUBLIC_PORT:
        return await PUBLIC_ASGI(scope, receive, send)
    if port == INTERNAL_PORT:
        return await INTERNAL_ASGI(scope, receive, send)
    if scope["type"] == "http":
        return await _reply(send, 404, "Not Found")
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})


def _listen(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    return s


def main() -> None:
    import uvicorn
    cfg = uvicorn.Config(app, lifespan="on", proxy_headers=False, server_header=False,
                         timeout_keep_alive=5, log_level="info")
    # One server, two sockets: single process, single writer of the RWO PVC.
    uvicorn.Server(cfg).run(sockets=[_listen(PUBLIC_PORT), _listen(INTERNAL_PORT)])


if __name__ == "__main__":
    main()
