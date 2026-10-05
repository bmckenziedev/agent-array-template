import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pace import KubernetesIdentity, Pace, reset_at, server_for, stamp

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/org.fixture.json').read_text())


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.now = [datetime(2026, 3, 7, 12, tzinfo=timezone.utc).timestamp()]
        self.review_calls = []
        fixture = FIXTURE
        parent = self
        class Review(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                parent.review_calls.append(body)
                token = body['spec']['token']
                identities = {'ana': 'system:serviceaccount:aa-u-ana:session',
                              'bo': 'system:serviceaccount:aa-u-bo:session',
                              'cy': 'system:serviceaccount:aa-u-cy:session',
                              'platform': 'system:serviceaccount:agent-array-system:farm-mcp',
                              'other': 'system:serviceaccount:agent-array-system:other',
                              'badns': 'system:serviceaccount:unowned:session',
                              'alias': 'system:serviceaccount:aa-u-alias:session',
                              'bad-audience': 'system:serviceaccount:aa-u-ana:session'}
                status = {'authenticated': token in identities,
                          'audiences': [] if token == 'bad-audience' else ['agent-array-pace'],
                          'user': {'username': identities.get(token, '')}}
                self.reply({'status': status})

            def do_GET(self):
                slug = self.path.rsplit('aa-u-', 1)[-1]
                if slug == 'alias':
                    slug = 'ana'
                self.reply({'metadata': {'labels': {
                    fixture['keys']['LABEL_PREFIX'] + '/kind': 'user-sessions',
                    fixture['keys']['LABEL_PREFIX'] + '/user': slug}}})

            def reply(self, value):
                payload = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        self.review = ThreadingHTTPServer(('127.0.0.1', 0), Review)
        self.review_thread = threading.Thread(target=self.review.serve_forever)
        self.review_thread.start()
        token_file = Path(self.temp.name) / 'token'
        token_file.write_text('test-platform-token')
        self.identity = KubernetesIdentity('http://127.0.0.1:' + str(self.review.server_port),
                                           'agent-array-pace', token_file, None,
                                           fixture['keys']['LABEL_PREFIX'], 'aa-u-')
        self.accounts = copy.deepcopy(fixture['accounts']['accounts'])
        self.policy = dict(fixture['accounts']['default_policy'], min_session_spacing_s=0)
        self.events = []
        self.database = str(Path(self.temp.name) / 'state.sqlite')
        self.pace = self.make_pace()
        self.server = server_for(self.pace, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        for account in self.accounts:
            if account['type'] == 'api':
                for window in account['windows']:
                    self.usage(account['id'], window, 0, 'platform')
        self.review_calls.clear()
        self.identity.cache.clear()

    def make_pace(self):
        return Pace(self.database, self.accounts, FIXTURE['users'], FIXTURE['teams'], self.identity,
                    self.policy, ['agent-array-system/farm-mcp'], FIXTURE['accounts']['plans'],
                    clock=lambda: self.now[0], audit=self.events.append)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.review.shutdown()
        self.review_thread.join()
        self.review.server_close()
        self.pace.close()
        self.temp.cleanup()

    def call(self, method, path, body=None, token='ana'):
        headers = {} if token is None else {'Authorization': 'Bearer ' + token}
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request('http://127.0.0.1:' + str(self.server.server_port) + path,
                                         data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                status, payload = error.code, error.read()
        try:
            payload = json.loads(payload) if payload else None
        except ValueError:
            payload = payload.decode()
        return status, payload

    def usage(self, account='acct-claude-seat-ana', window='weekly', used=40, token='ana'):
        return self.call('POST', '/v1/usage', {'account_id': account, 'window': window,
                         'used_pct': used, 'resets_at': stamp(self.now[0] + 86400),
                         'source': 'manual'}, token)

    def lease(self, account='acct-claude-seat-ana', token='ana'):
        return self.call('POST', '/v1/lease', {'account_id': account, 'kind': 'session',
                                             'pod': 'label-discovered-session'}, token)

    def test_health_and_accounts_proxy(self):
        self.assertEqual(self.call('GET', '/healthz', token=None)[0], 200)
        status, accounts = self.call('GET', '/v1/accounts', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(len(accounts), len(self.accounts))
        self.assertNotIn('secret', json.dumps(accounts))
        self.assertEqual(accounts[0]['windows'][0]['reserve_pct'], 20)

    def test_usage_and_lease_lifecycle(self):
        self.assertEqual(self.usage()[0], 204)
        status, lease = self.lease()
        self.assertEqual(status, 200)
        self.now[0] += 40
        renewed = self.call('POST', '/v1/lease/' + lease['lease_id'] + '/renew', {})
        self.assertEqual(renewed[0], 200)
        self.assertNotEqual(renewed[1]['expires_at'], lease['expires_at'])
        self.assertEqual(self.call('DELETE', '/v1/lease/' + lease['lease_id'])[0], 204)
        self.assertEqual(self.call('DELETE', '/v1/lease/' + lease['lease_id'])[0], 404)

    def test_auth_failures(self):
        for token in (None, 'invalid', 'bad-audience'):
            self.assertEqual(self.lease(token=token)[0], 401)
        self.assertEqual(self.lease(token='badns')[0], 403)
        self.assertEqual(self.lease(token='alias')[0], 403)
        self.assertEqual(self.lease(token='other')[0], 403)
        self.assertEqual(self.lease('acct-claude-seat-cy', 'cy')[0], 403)

    def test_cross_user_denied(self):
        self.assertEqual(self.usage(token='bo')[0], 403)
        self.assertEqual(self.lease(token='bo')[0], 403)
        lease = self.lease()[1]['lease_id']
        self.assertEqual(self.call('POST', '/v1/lease/' + lease + '/renew', {}, 'bo')[0], 403)
        self.assertEqual(self.call('DELETE', '/v1/lease/' + lease, token='bo')[0], 403)

    def test_platform_account_authorization(self):
        self.assertEqual(self.usage('acct-anthropic-api-payments', 'monthly')[0], 403)
        self.assertEqual(self.usage('acct-anthropic-api-payments', 'monthly', token='platform')[0], 204)
        self.assertEqual(self.usage('acct-anthropic-api-payments', 'monthly', token='other')[0], 403)
        self.assertEqual(self.usage(token='platform')[0], 403)

    def test_lease_race_is_serialized(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: self.lease(), range(10)))
        self.assertEqual(sum(status == 200 for status, _ in results), 3)
        self.assertEqual(sum(body == {'reason': 'max_concurrent'} for _, body in results), 7)

    def test_spacing_and_ttl(self):
        self.pace.policy['min_session_spacing_s'] = 30
        self.assertEqual(self.lease()[0], 200)
        self.assertEqual(self.lease(), (409, {'reason': 'spacing'}))
        self.now[0] += 30
        self.assertEqual(self.lease()[0], 200)
        self.now[0] += 301
        self.assertEqual(self.lease()[0], 200)
        accounts = self.call('GET', '/v1/accounts')[1]
        self.assertEqual(next(a for a in accounts if a['id'] == 'acct-claude-seat-ana')['leases_active'], 1)

    def test_cap_and_reset(self):
        self.assertEqual(self.usage(used=80)[0], 204)
        self.assertEqual(self.lease(), (409, {'reason': 'cap'}))
        self.now[0] += 86401
        self.assertEqual(self.lease()[0], 200)

    def test_overrides_and_reserve(self):
        self.assertEqual(self.usage('acct-claude-seat-bo', used=85, token='bo')[0], 204)
        self.assertEqual(self.lease('acct-claude-seat-bo', 'bo')[0], 200)
        self.pace.accounts['acct-claude-seat-bo']['policy']['reserve_pct'] = 20
        self.assertEqual(self.lease('acct-claude-seat-bo', 'bo'), (409, {'reason': 'cap'}))

    def test_daily_cap_and_day_reset(self):
        account = 'acct-chatgpt-seat-ana'
        self.assertEqual(self.usage(account, used=21)[0], 204)
        self.assertEqual(self.lease(account), (409, {'reason': 'cap'}))
        self.now[0] += 86401
        self.assertEqual(self.lease(account)[0], 200)

    def test_route_rank_and_seat_exclusion(self):
        self.usage('acct-anthropic-api-payments', 'monthly', 50, 'platform')
        status, result = self.call('POST', '/v1/route', {'team': 'payments', 'task_class': 'build',
                                                      'data_class': 'internal'})
        self.assertEqual(status, 200)
        ids = [a['id'] for a in result['accounts']]
        self.assertTrue(all('seat' not in item for item in ids))
        self.assertEqual(ids[-1], 'acct-anthropic-api-payments')
        self.assertEqual(len(ids), 3)

    def test_route_data_class_and_team(self):
        body = {'team': 'payments', 'task_class': 'build', 'data_class': 'confidential'}
        result = self.call('POST', '/v1/route', body)[1]
        self.assertTrue(all(a['vendor'] != 'openai' for a in result['accounts']))
        body['data_class'] = 'restricted'
        self.assertEqual(self.call('POST', '/v1/route', body)[0], 403)
        body.update(team='platform', data_class='internal')
        self.assertEqual(self.call('POST', '/v1/route', body)[0], 403)
        self.assertEqual(self.call('POST', '/v1/route', body, 'platform')[0], 200)

    def test_metrics_and_audit(self):
        for _ in range(4):
            self.lease()
        self.call('POST', '/v1/route', {'team': 'payments', 'task_class': 'build', 'data_class': 'internal'})
        status, text = self.call('GET', '/metrics', token=None)
        self.assertEqual(status, 200)
        for name in ('aa_pace_window_used_pct', 'aa_pace_window_cap_pct', 'aa_pace_leases_active',
                     'aa_pace_lease_denied_total', 'aa_pace_route_total', 'aa_pace_usage_age_seconds', 'aa_pace_account_info'):
            self.assertIn(name + '{', text)
        self.assertTrue(any(e['outcome'] == 'deny' for e in self.events))
        self.assertNotIn('test-platform-token', json.dumps(self.events))

    def test_persistence_restart(self):
        self.usage(used=43)
        lease = self.lease()[1]
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.pace.close()
        self.pace = self.make_pace()
        self.server = server_for(self.pace, ('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        accounts = self.call('GET', '/v1/accounts')[1]
        seat = next(a for a in accounts if a['id'] == 'acct-claude-seat-ana')
        self.assertEqual(seat['windows'][-1]['used_pct'], 43)
        self.assertEqual(seat['leases_active'], 1)
        self.assertEqual(self.call('POST', '/v1/lease/' + lease['lease_id'] + '/renew', {})[0], 200)

    def test_tokenreview_cache(self):
        self.usage()
        self.usage()
        self.assertEqual(len(self.review_calls), 1)
        self.assertEqual(self.review_calls[0]['spec']['audiences'], ['agent-array-pace'])

    def test_renewal_denied_after_cap_and_expiry(self):
        lease = self.lease()[1]['lease_id']
        self.usage(used=80)
        self.assertEqual(self.call('POST', '/v1/lease/' + lease + '/renew', {}),
                         (409, {'reason': 'cap'}))
        self.now[0] += 301
        self.assertEqual(self.call('POST', '/v1/lease/' + lease + '/renew', {})[0], 404)

    def test_cache_expiry_revalidates(self):
        import time
        self.usage()
        for key, value in list(self.identity.cache.items()):
            self.identity.cache[key] = (time.monotonic() - 1, value[1])
        self.usage()
        self.assertEqual(len(self.review_calls), 2)

    def test_route_excludes_capped_and_concurrent_accounts(self):
        account = 'acct-anthropic-api-payments'
        self.usage(account, 'monthly', 80, 'platform')
        body = {'team': 'payments', 'task_class': 'build', 'data_class': 'internal'}
        result = self.call('POST', '/v1/route', body)[1]
        self.assertNotIn(account, [a['id'] for a in result['accounts']])
        self.usage(account, 'monthly', 0, 'platform')
        self.pace.accounts[account]['max_concurrent_sessions'] = 1
        self.assertEqual(self.lease(account, 'platform')[0], 200)
        result = self.call('POST', '/v1/route', body)[1]
        self.assertNotIn(account, [a['id'] for a in result['accounts']])

    def test_unreported_and_expired_api_fail_closed(self):
        account = 'acct-anthropic-api-payments'
        with self.pace.lock:
            self.pace.db.execute("UPDATE usage SET source='unreported' WHERE account=?", (account,))
            self.pace.db.commit()
        self.assertEqual(self.lease(account, 'platform'), (409, {'reason': 'cap'}))
        self.usage(account, 'monthly', 0, 'platform')
        self.now[0] += 86401
        self.assertEqual(self.lease(account, 'platform'), (409, {'reason': 'cap'}))

    def test_rolling_usage_expires_each_delta(self):
        account = 'acct-anthropic-api-payments'
        with self.pace.lock:
            self.pace.accounts[account]['windows'] = [{'name': 'monthly', 'kind': 'rolling', 'duration_s': 3600}]
        self.usage(account, 'monthly', 20, 'platform')
        self.now[0] += 600
        self.usage(account, 'monthly', 30, 'platform')
        self.now[0] += 3001
        rows = self.call('GET', '/v1/accounts')[1]
        row = next(a for a in rows if a['id'] == account)
        self.assertEqual(row['windows'][0]['used_pct'], 10)
        self.now[0] += 601
        rows = self.call('GET', '/v1/accounts')[1]
        self.assertEqual(next(a for a in rows if a['id'] == account)['windows'][0]['used_pct'], 0)

    def test_route_respects_spacing(self):
        account = 'acct-anthropic-api-payments'
        self.pace.accounts[account]['policy']['min_session_spacing_s'] = 30
        self.lease(account, 'platform')
        body = {'team': 'payments', 'task_class': 'build', 'data_class': 'internal'}
        result = self.call('POST', '/v1/route', body)[1]
        self.assertNotIn(account, [a['id'] for a in result['accounts']])
        self.now[0] += 30
        result = self.call('POST', '/v1/route', body)[1]
        self.assertIn(account, [a['id'] for a in result['accounts']])

    def test_schema_migration_preserves_rolling_observation(self):
        self.server.shutdown()
        self.thread.join()
        account = 'acct-anthropic-api-payments'
        self.pace.accounts[account]['windows'] = [{'name': 'monthly', 'kind': 'rolling', 'duration_s': 3600}]
        with self.pace.lock:
            self.pace.db.execute("UPDATE usage SET used=37,source='manual' WHERE account=?", (account,))
            self.pace.db.execute('DROP TABLE rolling_samples')
            self.pace.db.execute('DROP TABLE rolling_events')
            self.pace.db.execute('PRAGMA user_version=1')
            self.pace.db.commit()
        self.pace.close()
        self.pace = self.make_pace()
        with self.pace.lock:
            self.pace.cleanup()
            self.assertEqual(self.pace.rows(self.pace.account(account))[0]['used_pct'], 37)
            self.assertEqual(self.pace.db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_invalid_input(self):
        self.assertEqual(self.usage(window='invalid')[0], 400)
        self.assertEqual(self.usage(used=-1)[0], 400)
        self.assertEqual(self.usage(used=float('nan'))[0], 400)
        self.assertEqual(self.usage(account='missing')[0], 404)
        self.assertEqual(self.call('POST', '/v1/usage', {})[0], 400)
        self.assertEqual(self.call('POST', '/unknown', {})[0], 404)
        self.assertEqual(self.call('POST', '/v1/route', {'team': 'payments',
                          'task_class': 'bad\tlabel', 'data_class': 'internal'})[0], 400)
        self.assertEqual(self.call('POST', '/v1/lease', {'account_id': 'acct-claude-seat-ana',
                                                      'kind': 'automation', 'pod': 'session'})[0], 400)


class WindowTests(unittest.TestCase):
    def test_daily_dst_short_and_long_days(self):
        for value, hours in [('2026-03-08T00:00:00-05:00', 23), ('2026-11-01T00:00:00-04:00', 25)]:
            now = datetime.fromisoformat(value).timestamp()
            self.assertEqual((reset_at('daily', now, 'America/New_York') - now) / 3600, hours)

    def test_weekly_monthly_and_rolling(self):
        now = datetime.fromisoformat('2026-03-07T12:00:00-05:00').timestamp()
        expected = datetime.fromisoformat('2026-03-09T00:00:00-04:00').timestamp()
        self.assertEqual(reset_at('weekly', now, 'America/New_York'), expected)
        self.assertEqual(reset_at('monthly', now, 'America/New_York'),
                         datetime.fromisoformat('2026-04-01T00:00:00-04:00').timestamp())
        self.assertEqual(reset_at('5h', now, 'America/New_York'), now + 18000)
        self.assertEqual(reset_at('rolling', now, 'UTC', 7200), now + 7200)


if __name__ == '__main__':
    unittest.main()
