"""Read-only stdio cluster MCP. Configuration authority stays outside the agent."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

# The module image also copies the stdlib runtime beside this component.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_template"))
from aa_mcp import Kubernetes, bounded_result, validate, PROTOCOLS

CONF = "/etc/hermes/arrayops-readonly.conf"
NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}
TOOLS = [
    {"name": "cluster_status", "description": "Read cluster node readiness and capacity",
     "inputSchema": NO_ARGS, "annotations": {"readOnlyHint": True}},
    {"name": "prom_query", "description": "One read-only Prometheus instant query",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "minLength": 1, "maxLength": 2000}},
         "required": ["query"], "additionalProperties": False}, "annotations": {"readOnlyHint": True}},
    {"name": "alerts", "description": "Active Alertmanager alerts",
     "inputSchema": NO_ARGS, "annotations": {"readOnlyHint": True}},
]


def require_readonly(path=CONF):
    config = Path(path)
    info = config.stat()
    # Root-owned projected ConfigMaps are acceptable, including their symlink layout.
    if info.st_mode & 0o022:
        raise ValueError("read-only mode file must not be group or world writable")
    if hasattr(info, "st_uid") and info.st_uid != 0:
        raise ValueError("read-only mode file must be root owned")
    text = config.read_text()
    if not re.search(r"(?m)^ARRAYOPS_READONLY=1\s*$", text):
        raise ValueError("read-only mode is required")
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))


class Arrayops:
    def __init__(self, kube, config, check=None):
        self.kube, self.config = kube, config
        self.check = check or require_readonly

    def call(self, name, args):
        self.check()
        definition = next((t for t in TOOLS if t["name"] == name), None)
        if definition is None:
            raise ValueError("unknown read-only tool")
        validate(args, definition["inputSchema"])
        if name == "cluster_status":
            nodes = self.kube.request("GET", "/api/v1/nodes")
            result = [{"name": n["metadata"]["name"], "conditions": n.get("status", {}).get("conditions", []),
                       "capacity": n.get("status", {}).get("capacity", {})} for n in nodes.get("items", [])]
        else:
            namespace = self.config["MONITORING_NAMESPACE"]
            service = self.config["PROMETHEUS_SERVICE" if name == "prom_query" else "ALERTMANAGER_SERVICE"]
            base = "/api/v1/namespaces/" + namespace + "/services/" + service + "/proxy"
            suffix = "/api/v1/query?" + urlencode({"query": args["query"]}) if name == "prom_query" else "/api/v2/alerts?active=true"
            result = self.kube.request("GET", base + suffix)
        return bounded_result(result)

    def handle(self, message):
        mid = message.get("id") if isinstance(message, dict) else None
        try:
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("invalid request")
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            method = message.get("method")
            if method == "initialize":
                requested = params.get("protocolVersion")
                result = {"protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[0],
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "arrayops", "version": "1.0.0"}}
            elif method == "tools/list":
                self.check()
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call(params.get("name"), params.get("arguments", {}))
            elif method in ("ping", "notifications/initialized"):
                result = {}
            else:
                raise ValueError("unknown method")
            return {"jsonrpc": "2.0", "id": mid, "result": result} if "id" in message else None
        except Exception:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "invalid or unavailable read-only request"}}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true", required=True)
    parser.parse_args(argv)
    config = require_readonly()
    server = Arrayops(Kubernetes(config["APISERVER_URL"]), config)
    for line in sys.stdin:
        if len(line.encode()) > 128 * 1024:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request too large"}}
        else:
            try:
                response = server.handle(json.loads(line))
            except ValueError:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if response is not None:
            print(json.dumps(response), flush=True)
    return 0
