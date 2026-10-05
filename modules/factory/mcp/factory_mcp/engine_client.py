"""Authenticated bounded factory HTTP API client; never invokes an engine subprocess."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib import request, error

TOKEN_FILE = "/var/run/agent-array/mcp-token/token"
MAX_BYTES = 28000


def availability() -> str:
    return "configured" if os.environ.get("FACTORY_API_URL") else "disabled: FACTORY_API_URL is unset"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    base = os.environ.get("FACTORY_API_URL")
    if not base:
        return {"ok": False, "disabled": True, "detail": "FACTORY_API_URL is unset; nothing was queued"}
    try:
        token = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("empty MCP token")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(base.rstrip("/") + path, data=data, method=method,
                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with request.urlopen(req, timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return {"ok": False, "truncated": True, "detail": "API response exceeds output limit"}
            return json.loads(raw)
    except error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "detail": "Factory API rejected the request"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "detail": "Factory API unavailable", "error_kind": type(exc).__name__}


def submit(payload: dict) -> dict:
    allowed = {key: payload[key] for key in ("estate_id", "template", "cards", "priority") if key in payload}
    return call("POST", "/v1/batches", allowed)


def results(batch_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", batch_id or ""):
        return {"ok": False, "detail": "invalid batch id"}
    return call("GET", "/v1/batches/" + batch_id)
