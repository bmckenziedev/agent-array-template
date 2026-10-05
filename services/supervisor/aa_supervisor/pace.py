"""Outbound pacing; auth failures never become availability advisories."""
import datetime
import json
from urllib.parse import quote


class LeaseDenied(Exception):
    pass


class Pace:
    def __init__(self, env, egress):
        self.env, self.egress = env, egress

    def request(self, method, path, payload=None):
        token_path = None if method == "GET" else self.env.get("AA_PACE_TOKEN_FILE",
            "/var/run/agent-array/pace-token/token")
        try:
            status, value = self.egress.request_json(method, self.env["AA_PACE_URL"].rstrip("/") + path,
                                                    payload, token_path=token_path)
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            raise LeaseDenied("token_or_response_invalid") from exc
        if status == 409:
            raise LeaseDenied(value.get("reason", "denied"))
        if status >= 400:
            raise LeaseDenied("authentication_or_service_refusal")
        return value

    def acquire(self):
        return self.request("POST", "/v1/lease", {"account_id": self.env["AA_ACCOUNT"],
            "kind": "session", "pod": self.env.get("AA_POD_NAME")})

    def renew(self, identifier):
        return self.request("POST", "/v1/lease/" + quote(identifier, safe="") + "/renew", {})

    def release(self, identifier):
        return self.request("DELETE", "/v1/lease/" + quote(identifier, safe=""))

    def advisory(self, policy):
        accounts = self.request("GET", "/v1/accounts")
        result = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for account in accounts:
            if account["id"] != self.env["AA_ACCOUNT"]:
                continue
            for window in account.get("windows", []):
                try:
                    age = (now - datetime.datetime.fromisoformat(window["updated_at"].replace("Z", "+00:00"))).total_seconds()
                except (ValueError, KeyError, TypeError):
                    age = float("inf")
                reason = "stale" if age > policy["spawn_advisory_stale_s"] else None
                if window.get("used_pct", 0) >= window.get("cap_pct", 100) - policy["spawn_advisory_margin_pct"]:
                    reason = "near_cap"
                if reason:
                    result.append({"account": account["id"], "window": window["name"],
                        **{k: window.get(k) for k in ["used_pct", "cap_pct", "resets_at"]}, "reason": reason})
        return result
