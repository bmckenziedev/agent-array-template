"""Every session call uses a private exec-auth context and a fixed namespace."""

import json
import subprocess
import shutil

from . import config


def run(args: list[str], *, data: bytes | None = None, interactive: bool = False) -> bytes:
    command = [shutil.which("kubectl") or "kubectl", "--kubeconfig", str(config.kubeconfig()),
               "--context", "agent-array-user", *args]
    result = subprocess.run(command, input=data, capture_output=not interactive, check=False,
                            timeout=900 if interactive else 120)
    if result.returncode:
        # Raw kubectl errors may include credential-plugin diagnostics; do not echo them.
        raise ValueError(f"kubectl {args[0]} failed (exit {result.returncode})")
    return result.stdout or b""


def get(args: list[str]) -> dict:
    return json.loads(run(args))


def login(cfg: dict) -> None:
    plugin = subprocess.run([shutil.which("kubectl") or "kubectl", "oidc-login", "--help"], capture_output=True)
    if plugin.returncode:
        raise ValueError("kubectl oidc-login is required. Install kubelogin using "
                         "kubectl krew install oidc-login, or install the int128/kubelogin "
                         "binary as kubectl-oidc_login on PATH.")
    exec_auth = {
        "apiVersion": "client.authentication.k8s.io/v1",
        "command": "kubectl", "interactiveMode": "IfAvailable",
        "args": ["oidc-login", "get-token", f"--oidc-issuer-url={cfg['oidc_issuer']}",
                 f"--oidc-client-id={cfg['oidc_client_id']}", "--grant-type=device-code",
                 "--oidc-extra-scope=groups"],
    }
    data = {
        "apiVersion": "v1", "kind": "Config", "current-context": "agent-array-user",
        "clusters": [{"name": "agent-array", "cluster": {
            "server": cfg["cluster_api_url"],
            "certificate-authority": str(__import__('pathlib').Path(cfg['ca_bundle_path']).expanduser().resolve()),
        }}],
        "users": [{"name": "oidc-user", "user": {"exec": exec_auth}}],
        "contexts": [{"name": "agent-array-user", "context": {
            "cluster": "agent-array", "user": "oidc-user",
        }}],
    }
    config.write_json(config.kubeconfig(), data)
    # Trigger the exec flow instead of declaring success before authentication.
    run(["auth", "whoami", "-o", "json"])


class Identity:
    def __init__(self, cfg: dict, namespace: str | None = None):
        self.cfg = cfg
        info = get(["auth", "whoami", "-o", "json"])["status"]["userInfo"]
        cm = get(["get", "configmap", "org-directory", "-n", cfg["NS_SYSTEM"], "-o", "json"])
        self.directory = {key: json.loads(value) for key, value in cm["data"].items()
                          if key.endswith(".json")}
        metadata = get(["get", "configmap", "aa-client-directory", "-n", cfg["NS_SYSTEM"], "-o", "json"])
        identities = json.loads(metadata["data"]["identities.json"])
        registration = identities.get(info["username"])
        if not registration:
            raise ValueError("OIDC subject is not registered in aa-client-directory")
        users = self.directory["users.json"]
        username_prefix = json.loads(metadata["data"].get("username-prefix.json", '"oidc:"'))
        subject = info["username"]
        if not subject.startswith(username_prefix):
            raise ValueError("OIDC username prefix mismatch")
        subject = subject[len(username_prefix):]
        candidates = [u for u in users if u["oidc_sub"] == subject]
        if len(candidates) != 1:
            raise ValueError("OIDC subject is not uniquely registered in org-directory")
        self.user = dict(candidates[0], max_replicas_per_tool=registration["max_replicas_per_tool"])
        if self.user.get("status") != "active":
            raise ValueError("user is suspended or offboarded")
        self.slug = self.user["slug"]
        self.namespace = cfg["user_namespace_prefix"] + self.slug
        if not self.namespace.startswith(cfg["user_namespace_prefix"]):
            raise ValueError("directory namespace is outside the user namespace prefix")
        if namespace and namespace != self.namespace:
            raise ValueError("namespace confinement: only the caller's namespace is permitted")

    def scoped(self, args: list[str], **kwargs) -> bytes:
        if any(x in ("-n", "--namespace", "-A", "--all-namespaces", "--context", "--kubeconfig")
               or x.startswith(("--namespace=", "--context=", "--kubeconfig=")) for x in args):
            raise ValueError("namespace or context overrides are forbidden")
        return run(["-n", self.namespace, *args], **kwargs)

    def select(self, tool: str) -> str:
        if tool not in self.user["tools"]:
            raise ValueError("tool is not enabled for this user")
        p = self.cfg["label_prefix"]
        return f"{p}/user={self.slug},{p}/tool={tool}"

    def resources(self, kind: str, tool: str) -> list[dict]:
        data = json.loads(self.scoped(["get", kind, "-l", self.select(tool), "-o", "json"]))
        items = data.get("items", [])
        p = self.cfg["label_prefix"]
        for obj in items:
            meta = obj["metadata"]
            if (meta.get("namespace") != self.namespace
                    or meta.get("labels", {}).get(p + "/user") != self.slug
                    or meta.get("labels", {}).get(p + "/tool") != tool):
                raise ValueError("resource identity mismatch")
        return sorted(items, key=lambda o: o["metadata"]["name"])

    def pod(self, tool: str, pod: str | None = None) -> str:
        items = self.resources("pods", tool)
        ready = [o for o in items if o.get("status", {}).get("phase") == "Running"
                 and not o["metadata"].get("deletionTimestamp")]
        if pod:
            ready = [o for o in ready if o["metadata"]["name"] == pod]
        if len(ready) != 1:
            raise ValueError("select one running session with --pod from sessions list")
        return ready[0]["metadata"]["name"]

    def account(self, tool: str) -> dict:
        account_id = self.user["tools"][tool]["account"]
        accounts = self.directory["accounts.json"]
        if isinstance(accounts, dict):
            accounts = accounts["accounts"]
        found = [a for a in accounts if a["id"] == account_id
                 and a["type"] == "seat" and a["holder"] == self.slug]
        if len(found) != 1:
            raise ValueError("tool must be bound to the caller's seat account")
        return found[0]

    def tier_max(self) -> int:
        direct = self.user.get("max_replicas_per_tool")
        if direct is not None:
            return int(direct)
        sessions = self.directory.get("sessions.json", {})
        teams = {t["id"]: t for t in self.directory["teams.json"]}
        tier = self.user.get("tier") or teams[self.user["primary_team"]].get("session_tier")
        maximum = sessions.get("tiers", {}).get(tier, {}).get("max_replicas_per_tool")
        if maximum is None:
            raise ValueError("org-directory lacks the tier maximum; scaling refused")
        return int(maximum)
