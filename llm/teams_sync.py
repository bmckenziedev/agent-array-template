#!/usr/bin/env python3
"""Reconcile managed LiteLLM teams/users. Exit 0 success, 1 invalid input/API failure."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Admin authorization must never follow an endpoint redirect.
        return None


class API:
    def __init__(self, base, key):
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            raise ValueError("Invalid API URL")
        self.base, self.key = base.rstrip("/"), key
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, data=None):
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers={"Authorization": "Bearer " + self.key,
                                                  "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("API response too large")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            # Server bodies may contain credentials; never include them in errors.
            status = error.code
            error.close()
            raise ValueError(f"API {method} {path.split('?')[0]} returned {status}") from None
        except (urllib.error.URLError, json.JSONDecodeError):
            raise ValueError("API connection or response failed") from None

    def list_all(self, kind):
        items, page = [], 1
        while True:
            result = self.request("GET", f"/{kind}/list?page={page}&page_size=100")
            if isinstance(result, list):
                items.extend(result)
                break
            batch = result.get("data", result.get(kind + "s", []))
            if not isinstance(batch, list):
                raise ValueError("Invalid list response")
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            if page > 1000:
                raise ValueError("API pagination limit")
        return items


def plan(desired, current, allow_delete=False):
    actions = []
    # Users must exist before their team membership is assigned.
    for kind in ("user", "team"):
        identity = kind + "_id"
        existing = {item[identity]: item for item in current[kind + "s"]}
        wanted = {item[identity]: item for item in desired[kind + "s"]}
        for ident, item in sorted(wanted.items()):
            old = existing.get(ident)
            if old is not None and old.get("metadata", {}).get("managed_by") != "llm":
                raise ValueError(f"Refuse unmanaged {kind} collision: {ident}")
            payload = {k: v for k, v in item.items() if k not in ("teams", "members_with_roles")}
            if old is None or any(old.get(key) != value for key, value in payload.items()):
                actions.append({"action": "create" if old is None else "update",
                                "kind": kind, "id": ident, "payload": payload})
            if kind == "team":
                old_members = (old or {}).get("members_with_roles", [])
                if isinstance(old_members, str):
                    old_members = json.loads(old_members)
                old_members = {m["user_id"]: m["role"] for m in old_members}
                wanted_members = {m["user_id"]: m["role"] for m in item["members_with_roles"]}
                for user_id, role in sorted(wanted_members.items()):
                    if old_members.get(user_id) != role:
                        actions.append({"action": "member-add" if user_id not in old_members else "member-update",
                                        "kind": "team", "id": ident,
                                        "payload": {"team_id": ident, "user_id": user_id, "role": role}})
                for user_id in sorted(old_members.keys() - wanted_members.keys()):
                    actions.append({"action": "member-delete", "kind": "team", "id": ident,
                                    "payload": {"team_id": ident, "user_id": user_id}})
        for ident, item in sorted(existing.items()):
            if ident not in wanted and item.get("metadata", {}).get("managed_by") == "llm":
                actions.append({"action": "delete" if allow_delete else "delete-guarded",
                                "kind": kind, "id": ident})
    # Delete teams before users to avoid dangling memberships.
    return [a for a in actions if not a["action"].startswith("delete")] + sorted(
        [a for a in actions if a["action"].startswith("delete")], key=lambda a: (a["kind"], a["id"]))


def apply(api, actions):
    for action in actions:
        verb, kind = action["action"], action["kind"]
        if verb == "delete-guarded":
            continue
        if verb == "delete":
            api.request("POST", f"/{kind}/delete", {kind + "_ids": [action["id"]]})
        elif verb.startswith("member-"):
            payload = dict(action["payload"])
            operation = verb.split("-", 1)[1]
            if operation == "add":
                payload = {"team_id": payload["team_id"], "member": {
                    "user_id": payload["user_id"], "role": payload["role"]}}
            api.request("POST", "/team/member_" + operation, payload)
        else:
            payload = dict(action["payload"])
            if kind == "user" and verb == "create":
                payload["auto_create_key"] = False
            api.request("POST", f"/{kind}/{'new' if verb == 'create' else 'update'}", payload)


def report_usage(api, pace, accounts):
    """Use account-labelled virtual-key spend; never mix accounts in a team total."""
    keys = api.list_all("key")
    spend = {}
    for key in keys:
        metadata = key.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        account_id = metadata.get("account_id")
        if account_id:
            spend[account_id] = spend.get(account_id, 0) + float(key.get("spend", 0))
    now = datetime.now(timezone.utc)
    reset = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    for account in sorted(accounts, key=lambda a: a["id"]):
        if account["type"] not in ("api", "pool"):
            continue
        budget = account.get("budget_usd_per_month")
        windows = account.get("windows", [])
        if not windows:
            continue  # Unlimited pools have no pacing window to update.
        if not budget or budget <= 0:
            raise ValueError("Usage account requires a positive budget_usd_per_month")
        used = min(100, max(0, 100 * spend.get(account["id"], 0) / budget))
        for window in windows:
            name = window["name"] if isinstance(window, dict) else window
            if name != "monthly":
                raise ValueError("LiteLLM spend reporting supports monthly windows in v1")
            pace.request("POST", "/v1/usage", {"account_id": account["id"], "window": name,
                "used_pct": used, "resets_at": reset.isoformat().replace("+00:00", "Z"), "source": "litellm"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--file", default="rendered/files/llm/teams.json")
    commands = parser.add_subparsers(dest="command")
    for name in ("plan", "apply"):
        sub = commands.add_parser(name)
        sub.add_argument("--allow-delete", action="store_true")
        sub.add_argument("--check-secrets", action="store_true")
        if name == "apply":
            sub.add_argument("--yes", action="store_true")
    mint = commands.add_parser("mint-key-for")
    mint.add_argument("client", choices=("panel", "portal", "farm-mcp"))
    mint.add_argument("--seal-command", nargs=argparse.REMAINDER, required=True,
                      help="Sealing program and arguments; receives key on stdin, stdout suppressed")
    usage = commands.add_parser("report-usage")
    usage.add_argument("--pace-url", required=True)
    usage.add_argument("--accounts", default="/etc/agent-array/org/accounts.json")
    usage.add_argument("--pace-token", default="/var/run/agent-array/pace-token/token")
    args = parser.parse_args(argv)
    try:
        key = os.environ.get("LITELLM_MASTER_KEY")
        if not key:
            raise ValueError("LITELLM_MASTER_KEY environment variable is required")
        api = API(args.base_url, key)
        if args.command == "report-usage":
            pace = API(args.pace_url, Path(args.pace_token).read_text().strip())
            report_usage(api, pace, json.loads(Path(args.accounts).read_text()))
            return 0
        if args.command == "mint-key-for":
            if not args.seal_command:
                raise ValueError("A sealing command is required")
            service_id = "llm-mint-" + args.client
            old = next((u for u in api.list_all("user") if u["user_id"] == service_id), None)
            if old is not None and (old.get("metadata", {}).get("managed_by") != "llm-mint"
                                    or old.get("user_role") != "proxy_admin"):
                raise ValueError("Refuse unmanaged mint service identity")
            if old is None:
                api.request("POST", "/user/new", {"user_id": service_id,
                    "user_role": "proxy_admin", "auto_create_key": False,
                    "metadata": {"managed_by": "llm-mint"}})
            # Route-scoped admin credentials are trusted service credentials, not user keys.
            response = api.request("POST", "/key/generate", {
                "key_alias": args.client + "-mint",
                "user_id": service_id,
                "allowed_routes": ["/key/generate", "/key/delete", "/key/info"],
                "metadata": {"managed_by": "llm", "client": args.client}})
            if not isinstance(response.get("key"), str):
                raise ValueError("Mint response has no key")
            try:
                result = subprocess.run(args.seal_command, input=response["key"].encode(),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        check=False)
                sealed = result.returncode == 0
            except OSError:
                sealed = False
            if not sealed:
                api.request("POST", "/key/delete", {"keys": [response["key"]]})
                raise ValueError("Sealing failed; minted key revoked")
            namespace = "portal" if args.client in ("panel", "portal") else "system"
            print(f"Key sent privately to the sealing process. Seal using "
                  f"platform/sealed-secrets/seal.sh --namespace-ref {namespace} "
                  "--name litellm-mint --keys LITELLM_MINT_KEY; key output is suppressed.")
            return 0
        if args.command == "apply" and not args.yes:
            raise ValueError("apply requires --yes")
        with open(args.file, encoding="utf-8") as handle:
            desired = json.load(handle)
        if getattr(args, "check_secrets", False):
            for secret in desired["provider_secrets"]:
                print(f"Required Secret: {secret['namespace']}/{secret['name']} key={secret['key']}")
        current = {kind + "s": api.list_all(kind) for kind in ("team", "user")}
        actions = plan(desired, current, getattr(args, "allow_delete", False))
        for action in actions:
            details = json.dumps(action.get("payload", {}), sort_keys=True)
            print(f"{action['action']} {action['kind']} {action['id']} {details}")
        if args.command == "apply":
            apply(api, actions)
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
