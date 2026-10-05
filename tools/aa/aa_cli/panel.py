"""OIDC device flow with in-memory bearer credentials only."""

import json
import secrets
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("authenticated HTTP redirects are forbidden")


def request(url: str, *, data: bytes | None = None, headers: dict | None = None) -> bytes:
    if urlsplit(url).scheme != "https":
        raise ValueError("OIDC and portal endpoints require HTTPS")
    with build_opener(NoRedirect).open(Request(url, data=data, headers=headers or {}), timeout=60) as resp:
        result = resp.read(64 * 1024 * 1024 + 1)
        if len(result) > 64 * 1024 * 1024:
            raise ValueError("HTTP response exceeds transfer limit")
        return result


def form(url: str, fields: dict) -> dict:
    return json.loads(request(url, data=urlencode(fields).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded",
    }))


def device_token(cfg: dict) -> str:
    issuer = cfg["oidc_issuer"].rstrip("/")
    discovery = json.loads(request(issuer + "/.well-known/openid-configuration"))
    if discovery.get("issuer", "").rstrip("/") != issuer:
        raise ValueError("OIDC discovery issuer mismatch")
    auth = form(discovery["device_authorization_endpoint"], {
        "client_id": cfg["oidc_client_id"], "scope": "openid profile email groups",
    })
    print(f"Open {auth['verification_uri']} and enter {auth['user_code']}")
    deadline = time.monotonic() + min(int(auth["expires_in"]), 900)
    interval = max(int(auth.get("interval", 5)), 1)
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            token = form(discovery["token_endpoint"], {
                "client_id": cfg["oidc_client_id"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": auth["device_code"],
            })
        except HTTPError as exc:
            error = json.loads(exc.read(65536)).get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise ValueError("OIDC device authorization failed") from None
        if token.get("token_type", "").lower() != "bearer" or not token.get("access_token"):
            raise ValueError("OIDC issuer did not return a bearer access token")
        return token["access_token"]
    raise ValueError("OIDC device code expired")


class Client:
    def __init__(self, cfg: dict):
        if not cfg["panel_url"]:
            raise ValueError("panel URL is not configured")
        self.base = cfg["panel_url"].rstrip("/")
        self.token = device_token(cfg)

    def call(self, path: str, data: bytes | None = None, content_type: str = "application/json") -> bytes:
        return request(self.base + path, data=data, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": content_type,
        })

    def create_task(self, archive: bytes, estate_id: str, brief: str, origin: str = "") -> dict:
        boundary = "aa-" + secrets.token_hex(24)
        body = bytearray()
        for name, value in (("brief", brief), ("estate_id", estate_id), ("repos", origin)):
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(value.encode())
            body.extend(b"\r\n")
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="archive"; '
                    'filename="upload.tar"\r\nContent-Type: application/x-tar\r\n\r\n'.encode())
        body.extend(archive)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        return json.loads(self.call("/api/tasks", bytes(body), "multipart/form-data; boundary=" + boundary))
