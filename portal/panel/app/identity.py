"""Verified JWT identity and directory-backed task authorization."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import jwt


class AccessDenied(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class TokenVerifier:
    """Bounded JWKS cache; unknown kids refresh with an amplification limit."""

    def __init__(self, issuer: str, audience: str, jwks_url: str | None = None,
                 ttl: int = 3600, refresh_interval: int = 10, fetch=None):
        self.issuer, self.audience = issuer, audience
        self.jwks_url = jwks_url
        self.ttl, self.refresh_interval = ttl, refresh_interval
        self.fetch = fetch or self._fetch
        self.keys: dict = {}
        self.fetched = float('-inf')
        self.attempt = float('-inf')
        self.lock = threading.Lock()

    @staticmethod
    def _fetch(url: str) -> dict:
        # Redirects could disclose issuer metadata to an unintended endpoint.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        with opener.open(url, timeout=5) as response:
            raw = response.read((1 << 20) + 1)
        if len(raw) > 1 << 20:
            raise ValueError('identity response too large')
        return json.loads(raw)

    def _refresh(self, force: bool = False) -> None:
        with self.lock:
            now = time.monotonic()
            if self.keys and not force and now - self.fetched < self.ttl:
                return
            if now - self.attempt < self.refresh_interval:
                if now - self.fetched >= self.ttl:
                    self.keys = {}
                return
            self.attempt = now
            try:
                if self.jwks_url is None:
                    metadata = self.fetch(self.issuer.rstrip('/') + '/.well-known/openid-configuration')
                    if metadata.get('issuer') != self.issuer:
                        raise ValueError('discovery issuer mismatch')
                    url = metadata['jwks_uri']
                    if not url.startswith('https://'):
                        raise ValueError('JWKS requires HTTPS')
                    self.jwks_url = url
                document = self.fetch(self.jwks_url)
                if not isinstance(document, dict) or not isinstance(document.get('keys'), list):
                    raise ValueError('invalid JWKS document')
                keys = {}
                for key in document.get('keys', []):
                    if not isinstance(key, dict):
                        raise ValueError('invalid signing key')
                    if (key.get('kty') == 'RSA' and key.get('use', 'sig') == 'sig'
                            and key.get('alg', 'RS256') == 'RS256'
                            and isinstance(key.get('kid'), str)):
                        if key['kid'] in keys:
                            raise ValueError('duplicate signing kid')
                        keys[key['kid']] = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                if not keys:
                    raise ValueError('no signing keys')
                self.keys, self.fetched = keys, now
            except (OSError, ValueError, KeyError, TypeError, AttributeError, jwt.PyJWTError):
                # A failed refresh must not extend a stale key's lifetime.
                if now - self.fetched >= self.ttl:
                    self.keys = {}

    def verify(self, token: str | None) -> dict:
        if not token or len(token) > 16384:
            raise AccessDenied(401, 'missing or oversized identity token')
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
                raise ValueError('invalid signing algorithm')
            self._refresh()
            if header['kid'] not in self.keys:
                self._refresh(force=True)
            key = self.keys.get(header['kid'])
            if key is None:
                raise ValueError('unknown signing key')
            return jwt.decode(token, key, algorithms=['RS256'], issuer=self.issuer,
                              audience=self.audience,
                              options={'require': ['iss', 'aud', 'exp', 'sub']})
        except (ValueError, jwt.PyJWTError):
            raise AccessDenied(401, 'invalid identity token') from None


@dataclass(frozen=True)
class Identity:
    slug: str
    sub: str
    teams: tuple[str, ...]
    leads: tuple[str, ...]
    roles: tuple[str, ...]

    def allows(self, task: dict, write: bool = False, approve: bool = False) -> bool:
        # Auditor is a read-only constraint even when another group grants writes.
        if write and 'auditor' in self.roles:
            return False
        if 'platform-admin' in self.roles:
            return True
        if 'auditor' in self.roles:
            return not write
        if task.get('team') in self.leads:
            return True
        return not approve and task.get('owner') == self.slug


class Directory:
    def __init__(self, root: Path, groups_claim: str = 'groups',
                 admin_group: str = 'aa-platform-admin', auditor_group: str = 'aa-auditor',
                 access_kind: str = 'oidc-proxy'):
        self.root, self.groups_claim = root, groups_claim
        self.admin_group, self.auditor_group = admin_group, auditor_group
        self.access_kind = access_kind

    def rows(self, name: str) -> list[dict]:
        try:
            rows = json.loads((self.root / (name + '.json')).read_text(encoding='utf-8'))
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError('invalid directory')
            return rows
        except (OSError, ValueError):
            raise AccessDenied(403, 'org directory unavailable') from None

    def team(self, team_id: str) -> dict:
        matches = [team for team in self.rows('teams') if team.get('id') == team_id]
        if len(matches) != 1:
            raise AccessDenied(403, 'unknown team')
        return matches[0]

    def identify(self, claims: dict) -> Identity:
        field = 'email' if self.access_kind == 'cloudflare-access' else 'oidc_sub'
        claim = claims.get('email') if field == 'email' else claims.get('sub')
        if not isinstance(claim, str) or not claim:
            raise AccessDenied(403, 'missing identity claim')
        matches = [user for user in self.rows('users') if user.get(field) == claim]
        if (len(matches) != 1 or matches[0].get('status') != 'active'
                or matches[0].get('suspended', False)):
            raise AccessDenied(403, 'unknown or suspended user')
        user = matches[0]
        groups = claims.get(self.groups_claim, [])
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise AccessDenied(403, 'invalid groups claim')
        teams = tuple(sorted(team['id'] for team in self.rows('teams')
                             if team['id'] in user['teams'] and team['idp_group'] in groups))
        leads = tuple(team for team in teams if user['slug'] in self.team(team).get('leads', []))
        roles = ['member']
        if leads:
            roles.append('team-lead')
        if self.admin_group in groups:
            roles.append('platform-admin')
        if self.auditor_group in groups:
            roles.append('auditor')
        return Identity(user['slug'], claims['sub'], teams, leads, tuple(roles))
