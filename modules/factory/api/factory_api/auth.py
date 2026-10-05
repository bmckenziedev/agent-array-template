"""Fail-closed Kubernetes TokenReview authentication and directory mapping."""

import json
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class Denied(PermissionError):
    """The caller has no authenticated factory identity."""


class Kubernetes:
    def __init__(self, url, token_file, ca_file=None, timeout=10):
        self.url = url.rstrip("/")
        self.token_file = Path(token_file)
        self.context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        self.timeout = timeout

    def request(self, path, payload=None):
        token = self.token_file.read_text(encoding="utf-8").strip()
        request = Request(
            self.url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, context=self.context, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise Denied("authentication response exceeds limit")
                return json.loads(raw)
        except (HTTPError, URLError, ValueError, OSError) as error:
            raise Denied("identity service unavailable") from error

    def review(self, token, audience):
        spec = {"token": token}
        if audience is not None:
            spec["audiences"] = [audience]
        return self.request("/apis/authentication.k8s.io/v1/tokenreviews", {
            "apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview",
            "spec": spec,
        }).get("status", {})

    def namespace(self, name):
        return self.request("/api/v1/namespaces/" + quote(name, safe=""))


class Directory:
    def __init__(self, root):
        self.root = Path(root)

    def load(self):
        result = {}
        for name in ("users", "teams", "estates", "mcp-entitlements"):
            value = json.loads((self.root / (name + ".json")).read_text(encoding="utf-8"))
            result[name] = value
        return result


class Authenticator:
    def __init__(self, kube, directory, project_name, user_ns_prefix, label_prefix,
                 oidc_username_prefix="oidc:"):
        self.kube = kube
        self.directory = directory
        self.audience = project_name + "-mcp"
        self.user_ns_prefix = user_ns_prefix
        self.label_prefix = label_prefix
        self.oidc_username_prefix = oidc_username_prefix

    def authenticate(self, authorization):
        if not authorization.startswith("Bearer "):
            raise Denied("bearer token required")
        token = authorization[7:]
        if not token or len(token) > 16384 or any(c.isspace() for c in token):
            raise Denied("invalid bearer token")
        status = self.kube.review(token, self.audience)
        pod_audience_valid = bool(status.get("authenticated") and
                                  self.audience in status.get("audiences", []))
        if not pod_audience_valid:
            # Portal OIDC tokens use the API server's configured OIDC client audience.
            # Only mapped OIDC subjects may use this review; service accounts still
            # require the exact projected MCP audience below.
            status = self.kube.review(token, None)
            if not status.get("authenticated"):
                raise Denied("token audience or identity rejected")
        username = status.get("user", {}).get("username", "")
        directory = self.directory.load()
        users = directory["users"]
        if isinstance(users, dict):
            users = users.get("users", [])
        sa = None
        if username.startswith("system:serviceaccount:"):
            if not pod_audience_valid:
                raise Denied("MCP audience required for service accounts")
            parts = username.split(":")
            if len(parts) != 4 or parts[3] != "session":
                raise Denied("session service account required")
            namespace = parts[2]
            if not namespace.startswith(self.user_ns_prefix):
                raise Denied("user session namespace required")
            labels = self.kube.namespace(namespace).get("metadata", {}).get("labels", {})
            if labels.get(self.label_prefix + "/kind") != "user-sessions":
                raise Denied("namespace is not a user session")
            slug = labels.get(self.label_prefix + "/user")
            matches = [u for u in users if u["slug"] == slug]
            sa = namespace + "/session"
        else:
            # OIDC signatures/issuer/audience are verified by the API server, never decoded locally.
            matches = [u for u in users if username in (
                u.get("oidc_subject"), u.get("oidc_sub"), u.get("oidc_username"),
                self.oidc_username_prefix + u.get("oidc_sub", ""),
            ) and username]
        if len(matches) != 1 or matches[0].get("status") != "active":
            raise Denied("active directory user required")
        user = matches[0]
        return {
            "user": user["slug"], "sub": user["oidc_sub"], "sa": sa,
            "teams": list(user["teams"]), "primary_team": user["primary_team"],
        }, directory

    @staticmethod
    def authorize(identity, directory, team):
        if team not in identity["teams"]:
            raise Denied("team membership required")
        if "factory" not in directory["mcp-entitlements"].get(team, []):
            raise Denied("factory entitlement required")
