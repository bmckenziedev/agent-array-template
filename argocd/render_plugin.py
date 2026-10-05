"""Deterministic, group-based Argo CD application permissions."""
import json
import re

PLUGIN_NAME = 'argocd-rbac'


def atom(value):
    """Reject policy delimiters/wildcards rather than creating extra grants."""
    if not re.fullmatch(r'[A-Za-z0-9_.:@/-]+', value):
        raise ValueError('unsafe Argo RBAC identity or role identifier')
    return value


def render(model, emit):
    keys = model['keys']
    lines = [f"g, {atom(keys['GROUP_PLATFORM_ADMIN'])}, role:admin",
             f"g, {atom(keys['GROUP_AUDITOR'])}, role:readonly",
             f"g, {atom(keys['GROUP_BREAKGLASS'])}, role:admin"]
    lines += ['p, role:admin, applications, update/*, */*, allow',
              'p, role:admin, applications, delete/*, */*, allow',
              'p, role:admin, logs, get, */*, allow',
              'p, role:readonly, logs, get, */*, allow']
    users = {u['USER_SLUG']: u for u in model['entities']['user']}
    for team in sorted(model['entities']['team'], key=lambda t: t['TEAM_ID']):
        role = 'role:team-' + atom(team['TEAM_ID'])
        # Argo receives raw IdP groups; Kubernetes alone adds the OIDC prefix.
        lines.append(f"g, {atom(team['TEAM_IDP_GROUP'])}, {role}")
        members = sorted(set(json.loads(team['TEAM_MEMBERS_JSON'])) & users.keys())
        for slug in members:
            atom(slug)
            lines.append(f'p, {role}, applications, get, users/user-{slug}, allow')
        for lead in sorted(json.loads(team['TEAM_LEADS_JSON'])):
            if lead not in users or users[lead]['USER_STATUS'] != 'active':
                continue
            subject = atom(users[lead]['USER_OIDC_SUB'])
            lead_role = role + '-lead-' + lead
            lines.append(f'g, {subject}, {lead_role}')
            for slug in members:
                lines.append(f'p, {lead_role}, applications, sync, users/user-{slug}, allow')
    policy = '\n'.join(lines) + '\n'
    # JSON is valid YAML and avoids policy-string escaping ambiguities.
    obj = {'apiVersion': 'v1', 'kind': 'ConfigMap',
           'metadata': {'name': 'argocd-rbac-cm', 'namespace': keys['NS_ARGOCD']},
           'data': {'policy.csv': policy, 'policy.default': '', 'scopes': '[groups]'}}
    emit('global/argocd/k8s/argocd-rbac-cm.yaml', json.dumps(obj, indent=2, sort_keys=True) + '\n')
