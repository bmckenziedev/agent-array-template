"""Runtime RSA signatures, role matrix, bounded routes and real HTTP key minting."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'app'))
os.environ.update({
    'OIDC_ISSUER_URL': 'https://id.example.org', 'OIDC_CLIENT_ID': 'panel',
    'GROUP_PLATFORM_ADMIN': 'aa-platform-admin', 'GROUP_AUDITOR': 'aa-auditor',
    'PANEL_ORIGIN': 'https://panel.example.org',
    'LITELLM_ADMIN_BASE': 'http://127.0.0.1:4000', 'SESSION_JOBS_ENABLED': 'false',
})

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import main
from identity import AccessDenied, Directory, TokenVerifier


def signing_key(kid):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid=kid, use='sig', alg='RS256')
    return private, public


def asgi(method, path, token=None, body=b'', port=8080, extra=None):
    async def run():
        messages = []
        headers = [(b'authorization', ('Bearer ' + token).encode())] if token else []
        headers += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': method, 'path': path, 'raw_path': path.encode(),
                 'query_string': b'', 'scheme': 'https', 'headers': headers,
                 'server': ('localhost', port), 'client': ('127.0.0.1', 1234)}
        consumed = False

        async def receive():
            nonlocal consumed
            if consumed:
                return {'type': 'http.disconnect'}
            consumed = True
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            messages.append(message)

        await main.app(scope, receive, send)
        status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
        payload = b''.join(m.get('body', b'') for m in messages)
        return status, json.loads(payload) if payload.startswith(b'{') else payload
    return asyncio.run(run())


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key, cls.jwk = signing_key('first')
        cls.other_key, cls.other_jwk = signing_key('rotated')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = json.loads((HERE / 'fixtures/org.fixture.json').read_text())
        for name in ('users', 'teams'):
            (self.root / (name + '.json')).write_text(json.dumps(self.fixture[name]))
        (self.root / 'accounts.json').write_text(json.dumps(self.fixture['accounts']['accounts']))
        self.directory = Directory(self.root)
        self.document = {'keys': [self.jwk]}
        self.fetches = []

        def fetch(url):
            self.fetches.append(url)
            return self.document

        self.verifier = TokenVerifier('https://id.example.org', 'panel',
                                      'https://id.example.org/jwks', refresh_interval=0, fetch=fetch)
        self.patches = [patch.object(main, 'DIRECTORY', self.directory),
                        patch.object(main, 'VERIFIER', self.verifier),
                        patch.object(main, 'TASKS', self.root / 'tasks'),
                        patch.object(main, 'TOMBSTONES', self.root / 'purged.json'),
                        patch.object(main, 'SESSION_JOBS_ENABLED', True)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        main.TASKS.mkdir()

    def token(self, slug='bo', groups=None, **overrides):
        user = next(u for u in self.fixture['users'] if u['slug'] == slug)
        claims = {'iss': 'https://id.example.org', 'aud': 'panel', 'exp': time.time() + 300,
                  'sub': user['oidc_sub'], 'groups': groups or ['aa-team-payments']}
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm='RS256', headers={'kid': 'first'})

    def task(self, owner='ana', team='payments', stage='queued'):
        tid = secrets.token_hex(8)
        d = main.tdir(tid, create=True)
        (d / 'owner').write_text(owner)
        (d / 'token').write_text(secrets.token_hex(24))
        main.update(tid, stage, task_id=tid, owner=owner, team=team,
                    account_id='acct-local-gpu', data_class='internal', mode='standard',
                    created_ts=time.time())
        return tid

    def test_cloudflare_directory_uses_verified_email(self):
        directory = Directory(self.root, access_kind="cloudflare-access")
        user = next(u for u in self.fixture["users"] if u["slug"] == "bo")
        identity = directory.identify({"email": user["email"], "sub": "different-cloudflare-subject", "groups": ["aa-team-payments"]})
        self.assertEqual(identity.slug, "bo")

    def test_good_oidc_me_and_intersection(self):
        status, doc = asgi('GET', '/api/me', self.token(groups=['aa-team-payments', 'untrusted']))
        self.assertEqual(status, 200)
        self.assertEqual(doc['slug'], 'bo')
        self.assertEqual(doc['teams'], ['payments'])
        self.assertEqual(doc['roles'], ['member'])
        self.assertEqual(asgi('GET', '/api/me', self.token())[0], 200)
        self.assertEqual(len(self.fetches), 1)

    def test_wrong_issuer_audience_expiry_and_missing_subject(self):
        for claims in ({'iss': 'https://wrong.example.org'}, {'aud': 'other'},
                       {'exp': time.time() - 60}, {'sub': None}, {'nbf': time.time() + 60}):
            with self.subTest(claims=claims):
                self.assertEqual(asgi('GET', '/api/me', self.token(**claims))[0], 401)

    def test_unknown_kid_then_rotated_jwks(self):
        token = jwt.encode({'iss': self.verifier.issuer, 'aud': 'panel', 'exp': time.time() + 300,
                            'sub': 'new'}, self.other_key, algorithm='RS256', headers={'kid': 'rotated'})
        with self.assertRaises(AccessDenied):
            self.verifier.verify(token)
        self.document = {'keys': [self.other_jwk]}
        self.assertEqual(self.verifier.verify(token)['sub'], 'new')
        with self.assertRaises(AccessDenied):
            self.verifier.verify(self.token())

    def test_discovery_must_match_issuer_and_stale_keys_fail_closed(self):
        verifier = TokenVerifier('https://id.example.org', 'panel', fetch=lambda _: {
            'issuer': 'https://wrong.example.org', 'jwks_uri': 'https://wrong.example.org/jwks'})
        with self.assertRaises(AccessDenied):
            verifier.verify(self.token())
        self.verifier.verify(self.token())
        self.verifier.fetched = float('-inf')
        self.verifier.fetch = lambda _: (_ for _ in ()).throw(OSError())
        with self.assertRaises(AccessDenied):
            self.verifier.verify(self.token())

    def test_refresh_throttling_cannot_extend_stale_keys(self):
        self.verifier.verify(self.token())
        self.verifier.refresh_interval = 10
        self.verifier.fetched = time.monotonic() - self.verifier.ttl - 1
        self.verifier.attempt = time.monotonic()
        with self.assertRaises(AccessDenied):
            self.verifier.verify(self.token())

    def test_malformed_jwks_fails_closed(self):
        for document in ([], {'keys': [None]}, {'keys': []}):
            self.document = document
            with self.subTest(document=document), self.assertRaises(AccessDenied):
                self.verifier.verify(self.token())

    def test_cloudflare_access_signed_subject(self):
        verifier = TokenVerifier('https://example.cloudflareaccess.com', 'access-aud',
                                 'https://example.cloudflareaccess.com/cdn-cgi/access/certs',
                                 fetch=lambda _: self.document)
        token = self.token(iss=verifier.issuer, aud='access-aud')
        with patch.object(main, 'ACCESS_KIND', 'cloudflare-access'), patch.object(main, 'VERIFIER', verifier):
            self.assertEqual(asgi('GET', '/api/me', extra={'cf-access-jwt-assertion': token})[0], 200)
            self.assertEqual(asgi('GET', '/api/me', token)[0], 401)

    def test_unknown_and_suspended_users(self):
        self.assertEqual(asgi('GET', '/api/me', self.token(sub='unknown'))[0], 403)
        self.assertEqual(asgi('GET', '/api/me', self.token(slug='cy'))[0], 403)

    def test_role_list_get_cancel_matrix(self):
        matrix = [('member', 'bo', ['aa-team-payments'], False, False),
                  ('lead', 'ana', ['aa-team-payments'], True, True),
                  ('admin', 'bo', ['aa-platform-admin'], True, True),
                  ('auditor', 'bo', ['aa-auditor'], True, False)]
        for role, slug, groups, read, write in matrix:
            with self.subTest(role=role):
                tid = self.task(owner='third-user')
                token = self.token(slug, groups)
                status, doc = asgi('GET', '/api/tasks', token)
                self.assertEqual(status, 200)
                self.assertEqual(any(t['task_id'] == tid for t in doc['tasks']), read)
                self.assertEqual(asgi('GET', '/api/tasks/' + tid, token)[0], 200 if read else 403)
                self.assertEqual(asgi('POST', '/api/tasks/' + tid + '/cancel', token)[0], 200 if write else 403)
        tid = self.task(owner='bo')
        self.assertEqual(asgi('POST', '/api/tasks/' + tid + '/cancel', self.token())[0], 200)

    def test_lead_only_for_intersected_team_and_auditor_overrides_admin(self):
        tid = self.task(team='platform', owner='third-user')
        self.assertEqual(asgi('GET', '/api/tasks/' + tid, self.token('ana'))[0], 403)
        token = self.token(groups=['aa-platform-admin', 'aa-auditor'])
        self.assertEqual(asgi('GET', '/api/tasks/' + tid, token)[0], 200)
        self.assertEqual(asgi('POST', '/api/tasks/' + tid + '/cancel', token)[0], 403)

    def test_approval_requires_lead_and_review_bundle(self):
        tid = self.task(owner='bo', stage='needs-review')
        path = '/api/tasks/' + tid + '/approve'
        self.assertEqual(asgi('POST', path, self.token())[0], 403)
        self.assertEqual(asgi('POST', path, self.token('ana'))[0], 200)
        self.assertEqual(asgi('POST', path, self.token('ana'))[0], 409)

    def test_module_disabled_before_body_parsing(self):
        with patch.object(main, 'SESSION_JOBS_ENABLED', False):
            for method, path in [('GET', '/api/tasks'), ('POST', '/api/tasks'),
                                 ('POST', '/api/tasks/' + '1' * 16 + '/cancel')]:
                status, body = asgi(method, path, self.token(), b'invalid body')
                self.assertEqual(status, 501)
                self.assertIn('disabled', body['detail'])

    def test_socket_split_csrf_and_body_limit(self):
        self.assertEqual(asgi('GET', '/api/me', self.token(), port=8081)[0], 403)
        self.assertEqual(asgi('GET', '/internal/tasks/invalid/input', self.token())[0], 404)
        self.assertEqual(asgi('GET', '/healthz', port=9999)[0], 404)
        tid = self.task(owner='bo')
        self.assertEqual(asgi('POST', '/api/tasks/' + tid + '/cancel', self.token(),
                             extra={'origin': 'https://other.example.org'})[0], 403)
        self.assertEqual(asgi('POST', '/api/tasks', self.token(),
                             extra={'content-length': str(1 << 30)})[0], 413)

    def test_team_and_user_active_limits(self):
        self.task(owner='bo')
        with patch.object(main, 'MAX_ACTIVE_TASKS_PER_USER', 1):
            with self.assertRaises(main.HTTPException) as error:
                main.enforce_limits('bo', 'platform')
            self.assertEqual(error.exception.status_code, 429)
        with patch.object(main, 'MAX_ACTIVE_TASKS_PER_TEAM', 1):
            with self.assertRaises(main.HTTPException):
                main.enforce_limits('someone', 'payments')

    def test_audit_json_never_contains_tokens(self):
        tid = self.task(owner='bo')
        token = self.token()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            asgi('GET', '/api/tasks/' + tid, token)
            asgi('POST', '/api/tasks/' + tid + '/cancel', token)
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertGreater(len(lines), 1)
        self.assertNotIn(token, stream.getvalue())
        self.assertTrue(all(set(('ts', 'component', 'actor', 'target', 'outcome', 'detail')) <= line.keys()
                            for line in lines))

    def test_dispatch_credentials_only_in_runner_and_labels_on_job_and_pod(self):
        tid = self.task(owner='bo')
        template = self.root / 'template.yaml'
        template.write_text(json.dumps({
            'metadata': {'name': 'task-__TASK_ID__'},
            'spec': {'template': {'metadata': {}, 'spec': {
                'runtimeClassName': 'kata', 'automountServiceAccountToken': False,
                'initContainers': [{'name': 'tester', 'env': []}],
                'containers': [{'name': 'session', 'env': []}]}}}}))
        recorded = []
        fake = SimpleNamespace(create_namespaced_job=lambda ns, obj: recorded.append(obj))
        key = 'sk-' + secrets.token_hex(20)
        token = secrets.token_hex(24)
        with patch.object(main, 'TEMPLATE', template), patch.object(main, 'batch', fake), \
             patch.object(main, '_mint_task_key', return_value=(key, hashlib.sha256(key.encode()).hexdigest())):
            main._dispatch(tid, token, 'python -m unittest')
        job = recorded[0]
        for metadata in (job['metadata'], job['spec']['template']['metadata']):
            self.assertEqual(metadata['labels'][main.LABEL_PREFIX + '/user'], 'bo')
            self.assertEqual(metadata['labels'][main.LABEL_PREFIX + '/team'], 'payments')
            self.assertEqual(metadata['labels'][main.LABEL_PREFIX + '/account'], 'acct-local-gpu')
            self.assertEqual(metadata['labels'][main.LABEL_PREFIX + '/task'], tid)
        pod = job['spec']['template']['spec']
        env = {e['name']: e['value'] for e in pod['containers'][0]['env']}
        self.assertEqual(env['LITELLM_KEY'], key)
        self.assertEqual(env['TASK_TOKEN'], token)
        self.assertEqual(env['TASK_MODELS'], 'local-coder,local-coder-small')
        self.assertNotIn(key, json.dumps(pod['initContainers']))
        self.assertNotIn(token, json.dumps(pod['initContainers']))
        self.assertNotIn(key, json.dumps(main.read_status(tid)))

    def test_dispatch_create_failure_deletes_job_and_key(self):
        tid = self.task()
        deleted = []
        fake = SimpleNamespace(create_namespaced_job=lambda *args: (_ for _ in ()).throw(RuntimeError()))
        manifest = {'metadata': {'name': 'task-' + tid}, 'spec': {'template': {
            'spec': {'containers': [{'name': 'session'}]}}}}
        with patch.object(main, '_render_job', return_value=manifest), \
             patch.object(main, '_mint_task_key', return_value=('sk-test-task-key-value', 'digest')), \
             patch.object(main, 'batch', fake), \
             patch.object(main, '_delete_job', side_effect=lambda name: deleted.append(name)), \
             patch.object(main, '_delete_task_key') as revoke:
            with self.assertRaises(RuntimeError):
                main._dispatch(tid, 'token')
            revoke.assert_called_once_with(tid, 'digest')
            self.assertEqual(deleted, ['task-' + tid])

    def test_terminal_revokes_local_token_and_retries_model_key(self):
        tid = self.task()
        main.update(tid, litellm_key_hash='digest')
        main.update(tid, 'done')
        self.assertFalse((main.tdir(tid) / 'token').exists())
        with patch.object(main, '_delete_task_key', side_effect=['not deleted', None]) as revoke:
            self.assertEqual(main._revoke_task_key(tid), 'not deleted')
            main.reconcile_once()
            self.assertTrue(main.read_status(tid)['litellm_key_revoked'])
            self.assertEqual(revoke.call_count, 2)

    def test_empty_model_intersection_cannot_mint(self):
        tid = self.task()
        with patch.object(main, 'TASK_ALLOWED_MODELS', ('not-entitled',)):
            with self.assertRaises(main.KeyMintError):
                main._task_key_request(tid, 'standard')

    def test_archive_rejects_traversal_and_upload_token_after_terminal(self):
        for path in ('../secret', '/etc/secret', 'folder/.git/config', 'C:/secret'):
            with self.subTest(path=path), self.assertRaises(main.HTTPException):
                main._member_rel(path)
        tid = self.task()
        token = (main.tdir(tid) / 'token').read_text()
        main.check_token(tid, token)
        main.update(tid, 'error-cancelled')
        with self.assertRaises(main.HTTPException):
            main.check_token(tid, token)

    def test_mint_body_and_refused_scope_against_real_http_fake(self):
        tid = self.task(owner='bo', team='platform')
        calls = []
        override = {}
        mint_key = 'sk-' + secrets.token_hex(20)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, self.headers['Authorization'], body))
                doc = {'deleted_keys': []}
                if self.path == '/key/generate':
                    key = 'sk-' + secrets.token_hex(24)
                    doc = {**body, 'key': key, 'token': hashlib.sha256(key.encode()).hexdigest(),
                           'allowed_routes': ['llm_api_routes'],
                           'expires': (datetime.now(timezone.utc) + timedelta(
                               seconds=main.TASK_KEY_TTL_SECONDS)).isoformat(), **override}
                payload = json.dumps(doc).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            with patch.object(main, 'LITELLM_ADMIN_BASE', 'http://127.0.0.1:' + str(server.server_port)), \
                 patch.object(main, 'LITELLM_MINT_KEY', mint_key), \
                 patch.object(main, 'TASK_ALLOWED_MODELS', ('local-coder', 'not-entitled')):
                key, digest = main._mint_task_key(tid, 'bulk')
                path, authorization, body = calls[0]
                self.assertEqual(path, '/key/generate')
                self.assertEqual(authorization, 'Bearer ' + mint_key)
                self.assertEqual(body, {
                    'team_id': 'platform', 'user_id': 'bo', 'models': ['local-coder'],
                    'max_budget': 3, 'duration': str(main.TASK_KEY_TTL_SECONDS) + 's',
                    'key_alias': 'panel-' + tid, 'key_type': 'llm_api',
                    'metadata': {'task_id': tid, 'account_id': 'acct-local-gpu',
                                 'data_class': 'internal', 'client': 'panel'}})
                self.assertEqual(digest, hashlib.sha256(key.encode()).hexdigest())
                for field, value in [('models', []), ('team_id', 'wrong'), ('user_id', 'wrong'),
                                     ('max_budget', 900), ('expires', None), ('allowed_routes', [])]:
                    override.clear()
                    override[field] = value
                    with self.subTest(field=field), self.assertRaises(main.KeyMintError):
                        main._mint_task_key(tid, 'bulk')
                    self.assertEqual(calls[-1][0], '/key/delete')
        finally:
            server.shutdown()
            worker.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
