#!/usr/bin/env python3
"""Account pacing HTTP service; Python 3.10 standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import sqlite3
import ssl
import threading
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc


def stamp(value):
    return datetime.fromtimestamp(value, UTC).isoformat().replace('+00:00', 'Z')


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('resets_at requires a timezone')
    return result.timestamp()


def reset_at(name, now, tz, duration=None):
    """Calendar resets use local midnight; duration windows use UTC seconds."""
    local = datetime.fromtimestamp(now, (UTC if tz == 'UTC' else ZoneInfo(tz)))
    if name == 'monthly':
        year = local.year + (local.month == 12)
        month = 1 if local.month == 12 else local.month + 1
        return datetime(year, month, 1, tzinfo=local.tzinfo).timestamp()
    if name in ('weekly', 'daily'):
        days = 7 - local.weekday() if name == 'weekly' else 1
        day = local.date() + timedelta(days=days)
        return datetime.combine(day, datetime.min.time(), local.tzinfo).timestamp()
    seconds = duration or {'5h': 18000, 'rolling': 86400}.get(name)
    if not seconds or seconds <= 0:
        raise ValueError('unknown window duration')
    return now + seconds


class APIError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


class KubernetesIdentity:
    """TokenReview plus namespace ownership, with bounded successful review caching."""
    def __init__(self, endpoint, audience, token_file, ca_file, prefix, ns_prefix):
        self.endpoint = endpoint.rstrip('/')
        self.audience = audience
        self.token_file = Path(token_file)
        self.context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        self.prefix, self.ns_prefix = prefix, ns_prefix
        self.cache = {}
        self.lock = threading.Lock()

    def request(self, path, body=None):
        headers = {'Authorization': 'Bearer ' + self.token_file.read_text().strip()}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        request = urllib.request.Request(self.endpoint + path, data, headers)
        with urllib.request.urlopen(request, context=self.context, timeout=10) as reply:
            return json.load(reply)

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            cached = self.cache.get(digest)
        if cached and cached[0] > time.monotonic():
            return dict(cached[1])
        try:
            status = self.request('/apis/authentication.k8s.io/v1/tokenreviews', {
                'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenReview',
                'spec': {'token': token, 'audiences': [self.audience]}})['status']
            if not status.get('authenticated') or self.audience not in status.get('audiences', []):
                raise APIError(401, 'unauthenticated')
            username = status['user']['username']
            parts = username.split(':')
            if len(parts) != 4 or parts[:2] != ['system', 'serviceaccount']:
                raise APIError(403, 'service_account_required')
            ns, sa = parts[2:]
            actor = {'sa': ns + '/' + sa, 'user': None, 'sub': None}
            if sa == 'session':
                if not ns.startswith(self.ns_prefix):
                    raise APIError(403, 'namespace_denied')
                obj = self.request('/api/v1/namespaces/' + ns)
                labels = obj.get('metadata', {}).get('labels', {})
                if labels.get(self.prefix + '/kind') != 'user-sessions':
                    raise APIError(403, 'namespace_denied')
                actor['user'] = labels.get(self.prefix + '/user')
                if not actor['user']:
                    raise APIError(403, 'namespace_denied')
        except APIError:
            raise
        except (KeyError, OSError, ValueError):
            raise APIError(401, 'identity_unavailable')
        with self.lock:
            self.cache = {k: v for k, v in self.cache.items() if v[0] > time.monotonic()}
            if len(self.cache) >= 4096:
                self.cache.clear()
            self.cache[digest] = (time.monotonic() + 60, actor)
        return dict(actor)


class Pace:
    def __init__(self, database, accounts, users, teams, identity, policy=None,
                 platform_writers=(), plans=(), clock=time.time, audit=None):
        self.accounts = {a['id']: a for a in accounts}
        self.users = {u['slug']: u for u in users}
        self.teams = {t['id']: t for t in teams}
        self.policy = policy or {'cap_pct': 80, 'reserve_pct': 20, 'min_session_spacing_s': 30}
        self.plans, self.identity = list(plans), identity
        self.platform_writers = set(platform_writers)
        self.clock, self.audit_sink = clock, audit or (lambda event: print(json.dumps(event), flush=True))
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version > 2:
            raise ValueError('database schema is newer than this service')
        if version < 1:
            self.db.executescript('''
                CREATE TABLE usage(account TEXT, window TEXT, used REAL, reset REAL,
                    source TEXT, updated REAL, PRIMARY KEY(account,window));
                CREATE TABLE leases(id TEXT PRIMARY KEY, account TEXT, actor TEXT,
                    pod TEXT, expires REAL);
                CREATE TABLE starts(account TEXT PRIMARY KEY, started REAL);
                CREATE TABLE daily(account TEXT, day TEXT, spent REAL, last REAL,
                    reset REAL, PRIMARY KEY(account,day));
                CREATE TABLE counters(metric TEXT, labels TEXT, value INTEGER,
                    PRIMARY KEY(metric,labels));
                PRAGMA user_version=1;
            ''')
        if version < 2:
            self.db.executescript('''
                CREATE TABLE rolling_events(account TEXT, window TEXT, at REAL, delta REAL);
                CREATE INDEX rolling_events_at ON rolling_events(account,window,at);
                CREATE TABLE rolling_samples(account TEXT, window TEXT, used REAL,
                    PRIMARY KEY(account,window));
                PRAGMA user_version=2;
            ''')
        for account in accounts:
            (UTC if account.get('timezone', 'UTC') == 'UTC' else ZoneInfo(account.get('timezone', 'UTC')))
            for window in self.windows(account):
                self.db.execute('INSERT OR IGNORE INTO usage VALUES(?,?,?,?,?,?)', (
                    account['id'], window['name'], 0, self.next_reset(account, window),
                    'unreported', self.clock()))
        if version < 2:
            for account in accounts:
                for window in self.windows(account):
                    if not self.rolling_duration(window):
                        continue
                    row = self.db.execute(
                        'SELECT used,updated,source FROM usage WHERE account=? AND window=?',
                        (account['id'], window['name'])).fetchone()
                    if row and row[2] not in ('unreported', 'reset'):
                        self.db.execute('INSERT INTO rolling_events VALUES(?,?,?,?)',
                                        (account['id'], window['name'], row[1], row[0]))
                        self.db.execute('INSERT OR REPLACE INTO rolling_samples VALUES(?,?,?)',
                                        (account['id'], window['name'], row[0]))
        self.db.commit()

    def close(self):
        self.db.close()

    def windows(self, account):
        return [dict(w) if isinstance(w, dict) else {'name': w} for w in account.get('windows', [])]

    def rolling_duration(self, window):
        if window.get('kind') == 'rolling' or window['name'] == 'rolling':
            return window.get('duration_s', window.get('hours', 24) * 3600)
        return None

    def next_reset(self, account, window):
        duration = self.rolling_duration(window)
        if duration:
            return self.clock() + duration
        return reset_at(window['name'], self.clock(), account.get('timezone', 'UTC'),
                        window.get('duration_s'))

    def account_policy(self, account):
        return dict(self.policy, **account.get('policy', {}))

    def account(self, account_id):
        if account_id not in self.accounts:
            raise APIError(404, 'unknown_account')
        return self.accounts[account_id]

    def authorize(self, actor, account):
        if account['type'] == 'seat':
            user = self.users.get(actor.get('user'))
            if (not user or user.get('status') != 'active' or
                    account.get('holder') != user['slug'] or
                    actor['sa'] != self.identity.ns_prefix + user['slug'] + '/session'):
                raise APIError(403, 'holder_required')
        elif actor['sa'] not in self.platform_writers:
            raise APIError(403, 'platform_writer_required')

    def cleanup(self):
        now = self.clock()
        self.db.execute('DELETE FROM leases WHERE expires<=?', (now,))
        for account in self.accounts.values():
            for window in self.windows(account):
                duration = self.rolling_duration(window)
                if duration:
                    self.db.execute('DELETE FROM rolling_events WHERE account=? AND window=? AND at<=?',
                                    (account['id'], window['name'], now - duration))
                    total, oldest = self.db.execute(
                        'SELECT coalesce(sum(delta),0),min(at) FROM rolling_events WHERE account=? AND window=?',
                        (account['id'], window['name'])).fetchone()
                    self.db.execute('UPDATE usage SET used=?,reset=?,source=CASE WHEN updated<=? '
                                    "THEN 'reset' ELSE source END WHERE account=? AND window=?",
                                    (total, oldest + duration if oldest is not None else now + duration,
                                     now - duration, account['id'], window['name']))
                    continue
                self.db.execute('UPDATE usage SET used=0, reset=?, source=?, updated=? '
                                'WHERE account=? AND window=? AND reset<=?', (
                                    self.next_reset(account, window), 'reset', now,
                                    account['id'], window['name'], now))

    def counter(self, metric, labels):
        self.db.execute('INSERT INTO counters VALUES(?,?,1) ON CONFLICT(metric,labels) '
                        'DO UPDATE SET value=value+1', (metric, json.dumps(labels, sort_keys=True)))

    def rows(self, account):
        policy = self.account_policy(account)
        return [{'name': w, 'used_pct': used, 'cap_pct': policy['cap_pct'],
                 'reserve_pct': policy['reserve_pct'], 'resets_at': stamp(reset),
                 'source': source, 'updated_at': stamp(updated)}
                for w, used, reset, source, updated in self.db.execute(
                    'SELECT window,used,reset,source,updated FROM usage WHERE account=? ORDER BY window',
                    (account['id'],))]

    def active(self, account):
        return self.db.execute('SELECT count(*) FROM leases WHERE account=?', (account['id'],)).fetchone()[0]

    def headroom(self, account):
        policy = self.account_policy(account)
        cap = min(policy['cap_pct'], 100 - policy['reserve_pct'])
        rows = self.rows(account)
        if account['type'] != 'seat' and account['vendor'] != 'local' and any(
                row['source'] in ('unreported', 'reset') for row in rows):
            return -1
        return min([cap - w['used_pct'] for w in rows] or [cap])

    def daily_allowed(self, account):
        cap = account.get('daily_cap_pct_of_week')
        if cap is None:
            return True
        day = datetime.fromtimestamp(self.clock(), (UTC if account.get('timezone', 'UTC') == 'UTC' else ZoneInfo(account.get('timezone', 'UTC')))).date().isoformat()
        row = self.db.execute('SELECT spent FROM daily WHERE account=? AND day=?',
                              (account['id'], day)).fetchone()
        return not row or row[0] < cap

    def usage(self, actor, body):
        account = self.account(body['account_id'])
        self.authorize(actor, account)
        name = body['window']
        if name not in [w['name'] for w in self.windows(account)]:
            raise APIError(400, 'unknown_window')
        used = float(body['used_pct'])
        if not math.isfinite(used) or not 0 <= used <= 100:
            raise APIError(400, 'invalid_used_pct')
        reset = timestamp(body['resets_at'])
        if reset <= self.clock():
            raise APIError(400, 'reset_in_past')
        source = body['source']
        if not isinstance(source, str) or len(source) > 64 or not source:
            raise APIError(400, 'invalid_source')
        window = next(w for w in self.windows(account) if w['name'] == name)
        duration = self.rolling_duration(window)
        if duration:
            sample = self.db.execute('SELECT used FROM rolling_samples WHERE account=? AND window=?',
                                     (account['id'], name)).fetchone()
            delta = used if sample is None or used < sample[0] else used - sample[0]
            self.db.execute('INSERT INTO rolling_events VALUES(?,?,?,?)',
                            (account['id'], name, self.clock(), delta))
            self.db.execute('INSERT OR REPLACE INTO rolling_samples VALUES(?,?,?)',
                            (account['id'], name, used))
            used, oldest = self.db.execute(
                'SELECT coalesce(sum(delta),0),min(at) FROM rolling_events WHERE account=? AND window=?',
                (account['id'], name)).fetchone()
            reset = oldest + duration
        if name == 'weekly' and account.get('daily_cap_pct_of_week') is not None:
            day = datetime.fromtimestamp(self.clock(), (UTC if account.get('timezone', 'UTC') == 'UTC' else ZoneInfo(account.get('timezone', 'UTC')))).date().isoformat()
            old = self.db.execute('SELECT used,reset FROM usage WHERE account=? AND window=?',
                                  (account['id'], name)).fetchone()
            increase = max(0, used - old[0]) if old[1] == reset else used
            self.db.execute('INSERT INTO daily VALUES(?,?,?,?,?) ON CONFLICT(account,day) '
                            'DO UPDATE SET spent=spent+excluded.spent,last=excluded.last,reset=excluded.reset',
                            (account['id'], day, increase, used, reset))
        self.db.execute('UPDATE usage SET used=?,reset=?,source=?,updated=? WHERE account=? AND window=?',
                        (used, reset, source, self.clock(), account['id'], name))

    def lease(self, actor, body):
        account = self.account(body['account_id'])
        self.authorize(actor, account)
        if body.get('kind') != 'session' or not isinstance(body.get('pod'), str) or not body['pod']:
            raise APIError(400, 'invalid_lease')
        policy = self.account_policy(account)
        previous = self.db.execute('SELECT started FROM starts WHERE account=?', (account['id'],)).fetchone()
        reason = None
        limit = account.get('max_concurrent_sessions')
        if limit is not None and self.active(account) >= limit:
            reason = 'max_concurrent'
        elif previous and self.clock() - previous[0] < policy['min_session_spacing_s']:
            reason = 'spacing'
        elif self.headroom(account) <= 0 or not self.daily_allowed(account):
            reason = 'cap'
        if reason:
            self.counter('aa_pace_lease_denied_total', {'account': account['id'], 'reason': reason})
            raise APIError(409, reason)
        lease_id = str(uuid.uuid4())
        expires = self.clock() + 300
        self.db.execute('INSERT INTO leases VALUES(?,?,?,?,?)',
                        (lease_id, account['id'], actor['sa'], body['pod'], expires))
        self.db.execute('INSERT OR REPLACE INTO starts VALUES(?,?)', (account['id'], self.clock()))
        return {'lease_id': lease_id, 'expires_at': stamp(expires)}

    def lease_action(self, actor, lease_id, renew):
        row = self.db.execute('SELECT account,actor FROM leases WHERE id=?', (lease_id,)).fetchone()
        if not row:
            raise APIError(404, 'unknown_lease')
        self.authorize(actor, self.account(row[0]))
        if actor['sa'] != row[1]:
            raise APIError(403, 'lease_owner_required')
        if renew:
            expires = self.clock() + 300
            account = self.account(row[0])
            if self.headroom(account) <= 0 or not self.daily_allowed(account):
                self.counter('aa_pace_lease_denied_total', {'account': account['id'], 'reason': 'cap'})
                raise APIError(409, 'cap')
            self.db.execute('UPDATE leases SET expires=? WHERE id=?', (expires, lease_id))
            return {'lease_id': lease_id, 'expires_at': stamp(expires)}
        self.db.execute('DELETE FROM leases WHERE id=?', (lease_id,))
        return None

    def route(self, actor, body):
        team_id, task_class, data_class = body['team'], body['task_class'], body['data_class']
        team = self.teams.get(team_id)
        if not team:
            raise APIError(404, 'unknown_team')
        user = self.users.get(actor.get('user'))
        if actor['sa'] not in self.platform_writers and (not user or
                user.get('status') != 'active' or team_id not in user.get('teams', [])):
            raise APIError(403, 'team_denied')
        if data_class not in team.get('data_classes_allowed', []):
            raise APIError(403, 'data_class_denied')
        if not isinstance(task_class, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', task_class):
            raise APIError(400, 'invalid_task_class')
        vendors = team.get('vendors_allowed', {}).get(data_class, [])
        result = []
        for account in self.accounts.values():
            shared = account.get('shared_with', [])
            if (account['type'] not in ('api', 'pool') or account['vendor'] not in vendors or
                    (account.get('owner_team') != team_id and team_id not in shared and '*' not in shared)):
                continue
            headroom = self.headroom(account)
            limit = account.get('max_concurrent_sessions')
            started = self.db.execute('SELECT started FROM starts WHERE account=?', (account['id'],)).fetchone()
            spacing = self.account_policy(account)['min_session_spacing_s']
            spaced = started is None or self.clock() - started[0] >= spacing
            if (headroom > 0 and spaced and self.daily_allowed(account) and
                    (limit is None or self.active(account) < limit)):
                result.append({'id': account['id'], 'vendor': account['vendor'], 'headroom_pct': headroom})
        result.sort(key=lambda a: (-a['headroom_pct'], a['id']))
        self.counter('aa_pace_route_total', {'team': team_id, 'task_class': task_class,
                                          'result': 'available' if result else 'empty'})
        return {'accounts': result}

    def metrics(self):
        lines = []
        def emit(name, labels, value):
            values = ','.join(k + '=' + json.dumps(str(v)) for k, v in sorted(labels.items()))
            lines.append(name + '{' + values + '} ' + str(value))
        for account in sorted(self.accounts.values(), key=lambda a: a['id']):
            holder = account.get('holder') or ''
            owner = account.get('owner_team') or ''
            team = owner or self.users.get(holder, {}).get('primary_team', '')
            emit('aa_pace_account_info', {'account': account['id'], 'vendor': account['vendor'],
                 'type': account['type'], 'holder': holder, 'owner_team': owner,
                 'team': team, 'usage_source': account.get('usage_source', '')}, 1)
            for row in self.rows(account):
                labels = {'account': account['id'], 'window': row['name'],
                          'vendor': account['vendor'], 'type': account['type']}
                emit('aa_pace_window_used_pct', labels, row['used_pct'])
                emit('aa_pace_window_cap_pct', labels, row['cap_pct'])
                emit('aa_pace_usage_age_seconds', labels, max(0, self.clock() - timestamp(row['updated_at'])))
            emit('aa_pace_leases_active', {'account': account['id']}, self.active(account))
        for metric, labels, value in self.db.execute('SELECT metric,labels,value FROM counters ORDER BY metric,labels'):
            emit(metric, json.loads(labels), value)
        return '\n'.join(lines) + '\n'

    def dispatch(self, method, path, token, body):
        actor = {'user': None, 'sa': None, 'sub': None}
        with self.lock:
            self.cleanup()
            try:
                if method == 'GET' and path == '/healthz':
                    return 200, {'status': 'ok'}, 'application/json'
                if method == 'GET' and path == '/metrics':
                    return 200, self.metrics(), 'text/plain; version=0.0.4'
                # Service proxy authorizes the OIDC caller at the API server. It does
                # not forward bearer credentials; this read-only view contains no secrets.
                if method == 'GET' and path == '/v1/accounts':
                    result = []
                    for account in sorted(self.accounts.values(), key=lambda a: a['id']):
                        result.append({k: account.get(k) for k in (
                            'id', 'vendor', 'type', 'holder', 'owner_team', 'max_concurrent_sessions')})
                        result[-1].update(leases_active=self.active(account), windows=self.rows(account))
                    return 200, result, 'application/json'
                if not token:
                    raise APIError(401, 'bearer_required')
                actor = self.identity.authenticate(token)
                if method == 'POST' and path == '/v1/usage':
                    self.usage(actor, body)
                    result, status = None, 204
                elif method == 'POST' and path == '/v1/lease':
                    result, status = self.lease(actor, body), 200
                elif method == 'POST' and path == '/v1/route':
                    result, status = self.route(actor, body), 200
                elif path.startswith('/v1/lease/'):
                    suffix = path[len('/v1/lease/'):]
                    if method == 'POST' and suffix.endswith('/renew'):
                        result, status = self.lease_action(actor, suffix[:-6], True), 200
                    elif method == 'DELETE' and '/' not in suffix:
                        result, status = self.lease_action(actor, suffix, False), 204
                    else:
                        raise APIError(404, 'not_found')
                else:
                    raise APIError(404, 'not_found')
                self.audit_sink({'ts': stamp(self.clock()), 'component': 'pace', 'event': method.lower() + '.pace',
                                 'actor': actor, 'team': body.get('team'), 'target': {'path': path},
                                 'outcome': 'allow', 'detail': {}})
                return status, result, 'application/json'
            except (KeyError, ValueError, TypeError, OverflowError):
                self.audit_sink({'ts': stamp(self.clock()), 'component': 'pace',
                                 'event': method.lower() + '.pace', 'actor': actor, 'team': None,
                                 'target': {'path': path}, 'outcome': 'deny',
                                 'detail': {'reason': 'invalid_request'}})
                raise APIError(400, 'invalid_request')
            except APIError as exc:
                self.audit_sink({'ts': stamp(self.clock()), 'component': 'pace', 'event': method.lower() + '.pace',
                                 'actor': actor, 'team': None, 'target': {'path': path},
                                 'outcome': 'deny', 'detail': {'reason': exc.reason}})
                if path == '/v1/route' and body.get('team') in self.teams:
                    self.counter('aa_pace_route_total', {'team': body['team'],
                                 'task_class': 'denied', 'result': 'denied'})
                raise
            finally:
                self.db.commit()


def server_for(pace, address=('0.0.0.0', 8080)):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *args):
            pass

        def handle_request(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 65536:
                    raise APIError(413, 'body_too_large')
                body = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(body, dict):
                    raise APIError(400, 'object_required')
                header = self.headers.get('Authorization', '')
                token = header[7:] if header.startswith('Bearer ') else None
                status, value, content_type = pace.dispatch(self.command, self.path, token, body)
            except APIError as exc:
                status, value, content_type = exc.status, {'reason': exc.reason}, 'application/json'
            except (ValueError, UnicodeError):
                status, value, content_type = 400, {'reason': 'invalid_json'}, 'application/json'
            payload = b'' if status == 204 else (value.encode() if isinstance(value, str) else json.dumps(value).encode())
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = handle_request

    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = False
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org-dir', default='/etc/agent-array/org')
    parser.add_argument('--database', default='/var/lib/pace/pace.sqlite')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args(argv)
    directory = Path(args.org_dir)
    def load(name):
        return json.loads((directory / name).read_text(encoding='utf-8'))
    settings = load('pace.json')
    identity = KubernetesIdentity(os.environ.get('KUBERNETES_API', 'https://kubernetes.default.svc'),
                                  settings['audience'],
                                  '/var/run/secrets/kubernetes.io/serviceaccount/token',
                                  '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt',
                                  settings['label_prefix'], settings['user_ns_prefix'])
    pace = Pace(args.database, load('accounts.json'), load('users.json'), load('teams.json'), identity,
                settings['default_policy'], settings['platform_writers'], settings['plans'])
    server = server_for(pace, ('0.0.0.0', args.port))
    # signal handlers run on the main thread, so shutdown must run elsewhere.
    stopping = threading.Event()
    def stop(signum, frame):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.timeout = 0.5
    try:
        while not stopping.is_set():
            server.handle_request()
    finally:
        server.server_close()
        pace.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
