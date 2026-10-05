#!/usr/bin/env python3
"""Deterministic organization alert routing and metric-label compilation.

Outputs contain Secret references only. No credential files or APIs are read.
JSON is used as a YAML-compatible encoding to keep the plugin stdlib-only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_NAME = 'alertmanager'
HERE = Path(__file__).resolve().parent
KEY_RE = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')
NAME_RE = re.compile(r'^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$')


def subst(text: str, keys: dict[str, str]) -> str:
    def rep(match):
        if match.group(1) not in keys:
            raise KeyError(f'unknown placeholder {match.group(1)}')
        return str(keys[match.group(1)])
    return KEY_RE.sub(rep, text)


def encoded(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + '\n'


def secret_file(secret: str, key: str) -> str:
    if not NAME_RE.fullmatch(secret) or not re.fullmatch(r'[A-Za-z0-9_.-]+', key):
        raise ValueError('Invalid receiver Secret reference')
    return f'/etc/alertmanager/secrets/{secret}/{key}'


def receiver(entry: dict) -> dict:
    """Only allowlisted, nonsecret metadata is copied from organization inputs."""
    name, kind = entry['name'], entry['kind']
    if not isinstance(name, str) or not name or name in ['null', 'heartbeat']:
        raise ValueError('Invalid or reserved receiver name')
    path = secret_file(entry['secret'], entry['key'])
    result = {'name': name}
    if kind in ['webhook', 'ntfy']:
        # ntfy requires its Alertmanager-compatible publish URL/adapter in the Secret.
        result['webhook_configs'] = [dict(url_file=path, send_resolved=True,
                                          max_alerts=20, timeout='10s')]
    elif kind == 'slack':
        config = dict(api_url_file=path, send_resolved=True)
        if entry.get('channel'):
            config['channel'] = entry['channel']
        result['slack_configs'] = [config]
    elif kind == 'pagerduty':
        result['pagerduty_configs'] = [dict(routing_key_file=path, send_resolved=True)]
    elif kind == 'email':
        required = ['to', 'from', 'smarthost', 'auth_username']
        if any(not entry.get(field) for field in required):
            raise ValueError('Email receiver needs to/from/smarthost/auth_username')
        result['email_configs'] = [dict(
            **{field: entry[field] for field in required},
            auth_password_file=path, require_tls=True, send_resolved=True,
        )]
    else:
        raise ValueError(f'Unsupported receiver kind: {kind}')
    return result


def quiet_intervals(start: str, end: str, timezone: str) -> list[dict]:
    for value in [start, end]:
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', value):
            raise ValueError('Quiet hours must use HH:MM')
    # Alertmanager validates the IANA location; renderer hosts may lack tzdata.
    if not re.fullmatch(r'[A-Za-z0-9_+/-]+', timezone) or '..' in timezone:
        raise ValueError('Invalid IANA timezone name')
    if start == end:
        raise ValueError('Quiet start and end must differ')
    times = ([dict(start_time=start, end_time=end)] if start < end else
             [dict(start_time=start, end_time='24:00'), dict(start_time='00:00', end_time=end)])
    return [dict(name='quiet-hours', time_intervals=[dict(times=times, location=timezone)])]


def routes_for(name: str) -> list[dict]:
    return [
        dict(receiver=name, matchers=['severity = critical'], group_wait='0s',
             group_interval='5m', repeat_interval='1h'),
        dict(receiver='null', matchers=['severity =~ "info|none"']),
        dict(receiver=name, matchers=['severity !~ "critical|info|none"'],
             mute_time_intervals=['quiet-hours'], group_wait='5m',
             group_interval='30m', repeat_interval='12h'),
    ]


def build_config(model: dict) -> tuple[dict, list[str]]:
    alerting = model['org']['alerting']
    entries = sorted(alerting['receivers'], key=lambda entry: entry['name'])
    names = [entry['name'] for entry in entries]
    if len(set(names)) != len(names) or alerting['default_receiver'] not in names:
        raise ValueError('Receiver names must be unique and default must exist')
    receivers = [{'name': 'null'}] + [receiver(entry) for entry in entries]
    secrets = {entry['secret'] for entry in entries}
    heartbeat = alerting['heartbeat']
    routes = []
    if heartbeat['enabled']:
        secrets.add(heartbeat['secret'])
        receivers.append(dict(name='heartbeat', webhook_configs=[dict(
            url_file=secret_file(heartbeat['secret'], heartbeat['key']),
            send_resolved=False, max_alerts=1, timeout='10s')]))
        routes.append(dict(receiver='heartbeat', matchers=['alertname = Watchdog'],
                           group_wait='0s', group_interval='30s', repeat_interval='45s'))
    else:
        routes.append(dict(receiver='null', matchers=['alertname = Watchdog']))
    team_routes = model['org'].get('components', {}).get('monitoring', {}).get('team_receivers', {})
    teams = {team['id'] for team in model['teams']}
    for team, target in sorted(team_routes.items()):
        if team not in teams or target not in names:
            raise ValueError('Team route must reference a declared team and receiver')
        routes.append(dict(receiver=target, matchers=[f'team = {json.dumps(team)}'],
                           routes=routes_for(target)))
    routes.extend(routes_for(alerting['default_receiver']))
    quiet = alerting['quiet_hours']
    config = dict(
        global_={'resolve_timeout': '5m'},
        route=dict(receiver=alerting['default_receiver'], group_by=['team', 'namespace', 'alertname'],
                   group_wait='30s', group_interval='5m', repeat_interval='12h', routes=routes),
        receivers=receivers,
        time_intervals=quiet_intervals(quiet['start'], quiet['end'], quiet['timezone']),
        inhibit_rules=[
            dict(source_matchers=['severity = critical'], target_matchers=['severity =~ warning|info'],
                 equal=['team', 'namespace', 'alertname', 'account', 'server', 'node']),
            dict(source_matchers=['severity = warning'], target_matchers=['severity = info'],
                 equal=['team', 'namespace', 'alertname', 'account', 'server', 'node']),
            dict(source_matchers=['alertname = InfoInhibitor'], target_matchers=['severity = info'],
                 equal=['team', 'namespace']),
            dict(target_matchers=['alertname = InfoInhibitor']),
            dict(source_matchers=['alertname =~ "KubeNodeNotReady|KubeNodeUnreachable"', 'node =~ ".+"'],
                 target_matchers=['alertname =~ "KubePod.*|KubeContainer.*"', 'node =~ ".+"'], equal=['node']),
        ],
    )
    config['global'] = config.pop('global_')
    return config, sorted(secrets)


def account_team(model: dict, account: dict) -> str:
    if account['ACCOUNT_OWNER_TEAM']:
        return account['ACCOUNT_OWNER_TEAM']
    holder = account['ACCOUNT_HOLDER']
    users = model['entities']['user']
    return next((user['USER_PRIMARY_TEAM'] for user in users if user['USER_SLUG'] == holder), '')


def render(model: dict, emit) -> None:
    config, secrets = build_config(model)
    keys = dict(model['keys'])
    # KSM uses label_ plus the complete Kubernetes key with punctuation replaced.
    keys['C_MONITORING_KSM_LABEL_PREFIX'] = re.sub('[^a-zA-Z0-9_]', '_', keys['LABEL_PREFIX'])
    keys['C_MONITORING_KSM_RUNTIME_VM_LABEL'] = 'label_' + re.sub(
        '[^a-zA-Z0-9_]', '_', keys['LABEL_PREFIX'] + '/runtime-' + keys['RUNTIME_CLASS_VM'])
    defaults = {
        'RETENTION_DAYS': 15, 'OIDC_AUTH_PATH': '/authorize', 'OIDC_TOKEN_PATH': '/token',
        'OIDC_USERINFO_PATH': '/userinfo', 'LITELLM_BUDGET_METRIC': 'litellm_team_max_budget_metric',
        'LITELLM_REMAINING_BUDGET_METRIC': 'litellm_remaining_team_budget_metric',
        'LITELLM_TEAM_LABEL': 'team_alias', 'LEASE_DENIALS_THRESHOLD': 10,
        'MCP_AUTH_DENIALS_THRESHOLD': 10, 'FACTORY_STARVATION_MINUTES': 15,
    }
    for key, value in defaults.items():
        keys.setdefault('C_MONITORING_' + key, str(value))
    groups = keys['OIDC_GROUPS_CLAIM']
    # Raw IdP groups, rather than the API server's OIDC group prefixes.
    clauses = [f"contains({groups} || `[]`, '{keys['GROUP_PLATFORM_ADMIN']}') && 'Admin'",
               f"contains({groups} || `[]`, '{keys['GROUP_AUDITOR']}') && 'Viewer'"]
    for team in sorted(model['teams'], key=lambda item: item['id']):
        clauses.append(f"contains({groups} || `[]`, '{team['idp_group']}') && 'Viewer'")
    fragment = {'alertmanager': {'config': config, 'alertmanagerSpec': {'secrets': secrets}},
                'grafana': {'grafana.ini': {'auth.generic_oauth': {
                    'role_attribute_path': ' || '.join(clauses), 'role_attribute_strict': True}}}}
    emit('files/monitoring/helm/kube-prometheus-stack/routing.values.yaml', encoded(fragment))
    emit('global/monitoring/k8s/alertmanager-config.yaml', encoded(dict(
        apiVersion='v1', kind='ConfigMap', metadata=dict(name='alertmanager-config', namespace=keys['NS_MONITORING'],
            labels={'app.kubernetes.io/part-of':keys['PROJECT_NAME'],'app.kubernetes.io/component':'alertmanager'}),
        data={'alertmanager.yaml': encoded(config)})))
    # Dynamic required Secret references are emitted as metadata for deployment tooling.
    requirements = [dict(name=entry['secret'], namespace=keys['NS_MONITORING'], keys=[entry['key']],
                         purpose=f"Alert receiver {entry['name']}") for entry in model['org']['alerting']['receivers']]
    heartbeat = model['org']['alerting']['heartbeat']
    if heartbeat['enabled']:
        requirements.append(dict(name=heartbeat['secret'], namespace=keys['NS_MONITORING'],
                                 keys=[heartbeat['key']], purpose='External Watchdog heartbeat'))
    emit('files/monitoring/receiver-secrets.required.json', encoded(requirements))
    all_requirements = [dict(name='grafana-admin',namespace=keys['NS_MONITORING'],
                             keys=['admin-user','admin-password'],purpose='Grafana emergency administrator'),
                        dict(name='grafana-oidc',namespace=keys['NS_MONITORING'],
                             keys=['client-id','client-secret'],purpose='Grafana OIDC client'),
                        dict(name=keys.get('C_MONITORING_LITELLM_METRICS_SECRET','litellm-metrics-key'),
                             namespace=keys['NS_MONITORING'],
                             keys=[keys.get('C_MONITORING_LITELLM_METRICS_SECRET_KEY','token')],
                             purpose='LiteLLM metrics-only key')]
    emit('files/monitoring/secrets.required.yaml', encoded(dict(version=1,secrets=all_requirements+requirements)))
    modules = model['org'].get('modules', {})
    gates = {'gpu.rules.yaml': 'gpu-lanes', 'factory.rules.yaml': 'factory',
             'ci.rules.yaml': 'arc-ci'}
    for source in sorted((HERE / 'alerts/rules').glob('*.yaml')):
        if source.name in ('hostwatch.rules.yaml', 'factory.rules.yaml'):
            continue
        module = gates.get(source.name)
        if module and not modules.get(module, {}).get('enabled', False):
            continue
        emit('global/monitoring/k8s/' + source.name, subst(source.read_text(encoding='utf-8'), keys))
        rule_text = subst(source.read_text(encoding='utf-8'), keys).split('spec:\n', 1)[1]
        plain = '\n'.join(line[2:] if line.startswith('  ') else line for line in rule_text.splitlines()) + '\n'
        emit('files/monitoring/alerts/rules/' + source.name, plain)
    for source in sorted((HERE / 'dashboards').glob('*.json')):
        dashboard = subst(source.read_text(encoding='utf-8'), keys)
        json.loads(dashboard)
        doc = dict(apiVersion='v1', kind='ConfigMap', metadata=dict(
            name='grafana-dashboard-' + source.stem, namespace=keys['NS_MONITORING'],
            labels={'grafana_dashboard': '1', 'app.kubernetes.io/part-of': keys['PROJECT_NAME'],
                    'app.kubernetes.io/component': 'dashboards'}), data={source.name: dashboard})
        emit('global/monitoring/k8s/dashboards/' + source.stem + '.yaml', encoded(doc))
    for server in sorted(model['entities']['mcp'], key=lambda item: item['MCP_NAME']):
        doc = dict(apiVersion='monitoring.coreos.com/v1', kind='ServiceMonitor', metadata=dict(
            name='mcp-' + server['MCP_NAME'], namespace=keys['NS_MONITORING'],
            labels={'app.kubernetes.io/part-of':keys['PROJECT_NAME']}), spec=dict(
                selector=dict(matchLabels={'app.kubernetes.io/name':server['MCP_APP_LABEL']}),
                namespaceSelector=dict(matchNames=[keys['NS_MCP']]),
                endpoints=[dict(port='http',path='/metrics',interval='30s',honorLabels=True)]))
        emit('global/monitoring/k8s/monitors/mcp-' + server['MCP_NAME'] + '.yaml', encoded(doc))
    # Account metadata is low cardinality and supplies missing team/usage-source labels.
    records = []
    for account in sorted(model['entities']['account'], key=lambda item: item['ACCOUNT_ID']):
        records.append(dict(record='aa_monitoring_account_info', expr='vector(1)', labels=dict(
            account=account['ACCOUNT_ID'], team=account_team(model, account),
            type=account['ACCOUNT_TYPE'], usage_source=account['ACCOUNT_USAGE_SOURCE'])))
    for user in sorted(model['entities']['user'],key=lambda item:item['USER_SLUG']):
        records.append(dict(record='aa_monitoring_namespace_info',expr='vector(1)',labels=dict(
            namespace=user['USER_NS'],user=user['USER_SLUG'],team=user['USER_PRIMARY_TEAM'])))
    if records:
        emit('global/monitoring/k8s/account-info.yaml', encoded(dict(
            apiVersion='monitoring.coreos.com/v1', kind='PrometheusRule', metadata=dict(
                name='account-info', namespace=keys['NS_MONITORING'],
                labels={'app.kubernetes.io/part-of': keys['PROJECT_NAME']}),
            spec=dict(groups=[dict(name='organization.accounts', rules=records)]))))
