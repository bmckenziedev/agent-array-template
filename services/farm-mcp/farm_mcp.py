#!/usr/bin/env python3
"""Authenticated cluster farm tools; seats remain exclusively interactive."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

# Both images ship the same reviewed transport and identity implementation.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp/servers/_template"))
from aa_mcp import Application, Auth, Directory, Kubernetes, json_http, make_server


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 24000}
ID = {"type": "string", "minLength": 1, "maxLength": 63}
CLASS = {"type": "string", "enum": ["public", "internal", "confidential", "restricted"]}
TASK_CLASS = {"type": "string", "enum": ["standard", "bulk", "build", "review", "live", "doc_map"]}
CALL_PROPERTIES = {"prompt": TEXT, "model": ID, "team": ID, "data_class": CLASS,
                   "task_class": TASK_CLASS, "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096}}
TOOLS = [
    {"name": "farm_status", "description": "Lane and queue summary, own sessions and permitted accounts",
     "inputSchema": schema(), "annotations": {"readOnlyHint": True}},
    {"name": "farm_sessions", "description": "List only caller-owned session pods",
     "inputSchema": schema(), "annotations": {"readOnlyHint": True}},
    {"name": "farm_llm", "description": "One paced completion on an entitled team model group",
     "inputSchema": schema(CALL_PROPERTIES, ("prompt", "model", "data_class"))},
    {"name": "farm_units", "description": "Submit doc_map cards to the optional factory API",
     "inputSchema": schema({"estate_id": ID, "cards": {"type": "array", "minItems": 1, "maxItems": 100},
                            "team": ID, "priority": {"type": "integer", "minimum": 0, "maximum": 100}},
                           ("estate_id", "cards"))},
    {"name": "farm_batch", "description": "Route and execute bounded API/pool calls through pace",
     "inputSchema": schema({"tasks": {"type": "array", "minItems": 1, "maxItems": 16}}, ("tasks",))},
    {"name": "farm_scale", "description": "Scale an own StatefulSet within tier and account ceilings",
     "inputSchema": schema({"statefulset": ID, "replicas": {"type": "integer", "minimum": 0, "maximum": 100}},
                           ("statefulset", "replicas"))},
]


class Farm:
    def __init__(self, kube, pace, litellm, factory, mint_key, config):
        self.kube, self.pace, self.litellm, self.factory = kube, pace, litellm, factory
        self.mint_key, self.config = mint_key, config
        self.label = config["label_prefix"]

    def team(self, actor, requested=None, data_class=None):
        name = requested or actor["user"]["primary_team"]
        if name not in actor["user"]["teams"]:
            raise ValueError("team is not available to caller")
        team = actor["teams"][name]
        if "farm" not in team["mcp_servers"]:
            raise ValueError("team has no farm entitlement")
        if data_class and data_class not in team["data_classes_allowed"]:
            raise ValueError("data class is not available to team")
        return team

    def visible_accounts(self, actor):
        return [a for a in actor["accounts"] if a.get("holder") == actor["user"]["slug"] or
                a.get("owner_team") in actor["user"]["teams"] or
                "*" in a.get("shared_with", []) or set(a.get("shared_with", [])) & set(actor["user"]["teams"])]

    def sessions(self, actor):
        selector = self.label + "/user=" + actor["user"]["slug"]
        response = self.kube.request("GET", "/api/v1/namespaces/" + actor["namespace"] +
                                     "/pods?" + urlencode({"labelSelector": selector}))
        return [{"name": p["metadata"]["name"], "phase": p.get("status", {}).get("phase"),
                 "tool": p["metadata"].get("labels", {}).get(self.label + "/tool"),
                 "account": p["metadata"].get("labels", {}).get(self.label + "/account")}
                for p in response.get("items", [])
                if p["metadata"].get("labels", {}).get(self.label + "/user") == actor["user"]["slug"]]

    def route(self, actor, team, task_class, data_class):
        response = self.pace("POST", "/v1/route", {"team": team["id"], "task_class": task_class,
                                                   "data_class": data_class})
        known = {a["id"]: a for a in actor["accounts"]}
        accounts = []
        for row in response.get("accounts", []):
            account = known.get(row.get("id"))
            if not account or account["type"] not in ("api", "pool"):
                continue
            if account["id"] not in team["pools"]:
                continue
            if account.get("owner_team") != team["id"] and team["id"] not in account.get("shared_with", []) and "*" not in account.get("shared_with", []):
                continue
            if account["vendor"] not in team["vendors_allowed"].get(data_class, []):
                continue
            accounts.append(account)
        if not accounts:
            raise ValueError("no eligible API/pool account; seats are interactive only")
        return accounts

    def llm(self, args, actor):
        team = self.team(actor, args.get("team"), args["data_class"])
        task_class = args.get("task_class", "standard")
        accounts = self.route(actor, team, task_class, args["data_class"])
        model = args["model"]
        if model not in team["litellm"]["models"]:
            raise ValueError("model group is not available to team")
        account = next((a for a in accounts if model in a["litellm_models"]), None)
        if not account:
            raise ValueError("model group is not available for routed account and data class")
        task_id = uuid.uuid4().hex
        # The lease service owns all concurrency counters, including across farm replicas.
        lease = self.pace("POST", "/v1/lease", {"account_id": account["id"], "kind": "session",
                                               "pod": "farm-" + task_id})
        lease_id = lease["lease_id"]
        key = None
        try:
            budget = team["litellm"]["task_budget_usd"].get(task_class,
                          team["litellm"]["task_budget_usd"]["standard"])
            minted = self.litellm("POST", "/key/generate", {
                "team_id": team["id"], "user_id": actor["user"]["slug"], "models": [model],
                "max_budget": budget, "duration": "120s", "key_alias": "farm-mcp-" + task_id,
                "metadata": {"task_id": task_id, "account_id": account["id"],
                             "data_class": args["data_class"], "client": "farm-mcp",
                             "user_id": actor["user"]["slug"], "team_id": team["id"]}}, self.mint_key)
            key = minted["key"]
            result = self.litellm("POST", "/v1/chat/completions", {
                "model": model, "messages": [{"role": "user", "content": args["prompt"]}],
                "max_tokens": args.get("max_tokens", 1400), "disable_fallbacks": True}, key)
            # Return the message only; provider response metadata must not leak keys.
            text = result["choices"][0]["message"].get("content", "")
            for credential in (key, self.mint_key, actor["token"]):
                if credential:
                    text = text.replace(credential, "[redacted]")
            return {"task_id": task_id, "account_id": account["id"], "model": model, "text": text}
        finally:
            try:
                if key:
                    self.litellm("POST", "/key/delete", {"keys": [key]}, self.mint_key)
            finally:
                self.pace("DELETE", "/v1/lease/" + quote(str(lease_id), safe=""))

    def units(self, args, actor):
        if not self.config["factory_enabled"]:
            raise ValueError("factory module is disabled; farm_units is unavailable")
        team = self.team(actor, args.get("team"))
        estate = next((e for e in self.config["estates"] if e["id"] == args["estate_id"]), None)
        if not estate or estate["owner_team"] != team["id"]:
            raise ValueError("estate is not owned by selected team")
        if estate["data_class"] not in team["data_classes_allowed"]:
            raise ValueError("estate data class is not available")
        if any(not isinstance(c, dict) for c in args["cards"]):
            raise ValueError("cards must be objects")
        priority = min(args.get("priority", 0), team["factory"]["queue_priority_ceiling"])
        return self.factory("POST", "/v1/batches", {"estate_id": estate["id"], "template": "doc_map",
                            "cards": args["cards"], "priority": priority, "team": team["id"]}, actor["token"])

    def scale(self, args, actor):
        name = args["statefulset"]
        if not all(c.islower() or c.isdigit() or c == "-" for c in name) or "/" in name:
            raise ValueError("invalid StatefulSet name")
        path = "/apis/apps/v1/namespaces/" + actor["namespace"] + "/statefulsets/" + name
        sts = self.kube.request("GET", path)
        labels = sts["metadata"].get("labels", {})
        user = actor["user"]
        if labels.get(self.label + "/user") != user["slug"]:
            raise ValueError("StatefulSet does not belong to caller")
        tool = labels.get(self.label + "/tool")
        spec = user["tools"].get(tool)
        if not spec or labels.get(self.label + "/account") != spec["account"]:
            raise ValueError("StatefulSet account is not bound to caller's tool")
        account = next(a for a in actor["accounts"] if a["id"] == spec["account"])
        if account["type"] != "seat" or account["holder"] != user["slug"]:
            raise ValueError("account is not caller's seat")
        tiers = self.config["tiers"]
        tier = user.get("tier") or actor["teams"][user["primary_team"]]["session_tier"]
        ceiling = min(tiers[tier]["max_replicas_per_tool"], account["max_concurrent_sessions"])
        desired = min(args["replicas"], ceiling)
        scale = self.kube.request("GET", path + "/scale")
        scale["spec"] = {"replicas": desired}
        # PUT preserves resourceVersion; a competing scale must fail instead of being overwritten.
        result = self.kube.request("PUT", path + "/scale", scale)
        return {"statefulset": name, "requested": args["replicas"], "replicas": result["spec"]["replicas"],
                "ceiling": ceiling}

    def call(self, name, args, actor):
        if name == "farm_sessions":
            return self.sessions(actor)
        if name == "farm_status":
            visible = {a["id"] for a in self.visible_accounts(actor)}
            rows = self.pace("GET", "/v1/accounts")
            # Explicit projection prevents pace implementation additions exposing credentials.
            fields = ("id", "vendor", "type", "holder", "owner_team", "max_concurrent_sessions",
                      "leases_active", "windows")
            accounts = [{k: a.get(k) for k in fields} for a in rows if a.get("id") in visible]
            batches = []
            if self.config["factory_enabled"]:
                for team_id in sorted(actor["user"]["teams"]):
                    if "farm" not in actor["teams"][team_id]["mcp_servers"]:
                        continue
                    response = self.factory("GET", "/v1/batches?" + urlencode({"team": team_id}),
                                            None, actor["token"])
                    items = response.get("batches") if isinstance(response, dict) else response
                    if not isinstance(items, list):
                        raise ValueError("factory queue response is invalid")
                    for batch in items:
                        if batch.get("team", team_id) == team_id:
                            batches.append({"batch_id": batch.get("batch_id"),
                                            "status": batch.get("status"), "team": team_id})
            return {"lanes": self.config["lanes"], "queue": {"leases_active": sum(
                    a.get("leases_active") or 0 for a in accounts), "batches": batches,
                    "factory_enabled": self.config["factory_enabled"]},
                    "accounts": accounts, "sessions": self.sessions(actor)}
        if name == "farm_llm":
            return self.llm(args, actor)
        if name == "farm_units":
            return self.units(args, actor)
        if name == "farm_scale":
            return self.scale(args, actor)
        if name == "farm_batch":
            from aa_mcp import validate
            # Validate all cards before issuing any call; local paths/modes cannot be smuggled in.
            for task in args["tasks"]:
                validate(task, schema(CALL_PROPERTIES, ("prompt", "model", "data_class")))
            results = []
            for task in args["tasks"]:
                try:
                    results.append({"result": self.llm(task, actor)})
                except ValueError as exc:
                    results.append({"error": str(exc)})
                except Exception:
                    results.append({"error": "upstream request failed"})
            return {"tasks": results}
        raise ValueError("unknown farm tool")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    config = json.loads(Path("/etc/agent-array/farm/config.json").read_text())
    kube = Kubernetes(os.environ["APISERVER_URL"])
    auth = Auth(kube, Directory(), os.environ["PROJECT_NAME"], os.environ["USER_NS_PREFIX"],
                os.environ["LABEL_PREFIX"], "farm")
    def api(base):
        return lambda method, path, body=None, token=None: json_http(base + path, method, body, token)
    pace_api = api(os.environ["PACE_URL"])
    def pace(method, path, body=None, token=None):
        credential = Path("/var/run/agent-array/pace-token/token").read_text().strip()
        return pace_api(method, path, body, credential)
    farm = Farm(kube, pace, api(os.environ["LITELLM_URL"]),
                api(os.environ["FACTORY_URL"]), Path("/etc/agent-array/mint/key").read_text().strip(), config)
    with make_server(Application("farm", auth, TOOLS, farm.call), ("0.0.0.0", args.port)) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
