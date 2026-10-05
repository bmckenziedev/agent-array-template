#!/usr/bin/env python3
"""audit_policy_check.py -- validate an audit.k8s.io/v1 Policy and prove what it logs.

Usage: audit_policy_check.py [--quiet] POLICY_YAML

1. Structural validation, close to kube-apiserver's own (k8s.io/apiserver
   pkg/apis/audit/validation): apiVersion/kind, levels, stages, rule keys, no rule
   mixing resources and nonResourceURLs, '*' only as the last character of a URL.
   kube-apiserver refuses to START with an invalid policy file, so on node-a this check
   runs before k3s is restarted (30-audit-logging.sh).
2. Evaluation with the apiserver's first-match semantics (pkg/audit/policy/checker.go:
   users, userGroups, verbs, namespaces, group/resource/subresource matching including
   "resource/subresource", "*/subresource" and "resource/*") against a table of
   requests with the level this repo expects (RUNBOOK.md, step 3).
3. Invariant: secrets, configmaps, serviceaccounts/token and tokenreviews are never
   logged above Metadata, for any verb and any user.

Exit 0 = valid and every expectation holds; 1 = an expectation or invariant failed;
2 = invalid policy or unreadable file. Needs PyYAML (python3-yaml; present on node-a).
"""
import sys

import yaml

LEVELS = ("None", "Metadata", "Request", "RequestResponse")
LEVEL_RANK = {lvl: n for n, lvl in enumerate(LEVELS)}
STAGES = ("RequestReceived", "ResponseStarted", "ResponseComplete", "Panic")
RULE_KEYS = {"level", "users", "userGroups", "verbs", "resources", "namespaces",
             "nonResourceURLs", "omitStages", "omitManagedFields"}
GR_KEYS = {"group", "resources", "resourceNames"}


class Invalid(Exception):
    pass


def validate(policy):
    if not isinstance(policy, dict):
        raise Invalid("not a mapping")
    if policy.get("apiVersion") != "audit.k8s.io/v1" or policy.get("kind") != "Policy":
        raise Invalid("apiVersion must be audit.k8s.io/v1 and kind Policy")
    for s in policy.get("omitStages", []) or []:
        if s not in STAGES:
            raise Invalid("unknown stage %r" % s)
    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        raise Invalid("rules must be a non-empty list")
    for n, r in enumerate(rules):
        where = "rule %d" % n
        if not isinstance(r, dict):
            raise Invalid(where + ": not a mapping")
        extra = set(r) - RULE_KEYS
        if extra:
            raise Invalid(where + ": unknown keys %s" % sorted(extra))
        if r.get("level") not in LEVELS:
            raise Invalid(where + ": level must be one of %s" % (LEVELS,))
        if r.get("nonResourceURLs") and (r.get("resources") or r.get("namespaces")):
            raise Invalid(where + ": nonResourceURLs cannot be combined with resources/namespaces")
        for url in r.get("nonResourceURLs", []) or []:
            if not url.startswith("/"):
                raise Invalid(where + ": nonResourceURL %r must start with /" % url)
            if "*" in url and url.index("*") != len(url) - 1:
                raise Invalid(where + ": '*' only allowed as the last character in %r" % url)
        for gr in r.get("resources", []) or []:
            if not isinstance(gr, dict) or set(gr) - GR_KEYS:
                raise Invalid(where + ": bad resources entry %r" % (gr,))
            for res in gr.get("resources", []) or []:
                if res.count("/") > 1 or (res.endswith("/*") and res.startswith("*/")):
                    raise Invalid(where + ": bad resource %r" % res)
            if gr.get("resourceNames") and any("/" in x for x in gr.get("resources", [])):
                raise Invalid(where + ": resourceNames cannot be used with subresources")
        for s in r.get("omitStages", []) or []:
            if s not in STAGES:
                raise Invalid(where + ": unknown stage %r" % s)
        for key in ("users", "userGroups", "verbs", "namespaces"):
            v = r.get(key)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                raise Invalid(where + ": %s must be a list of strings" % key)


def _matches_resource(rule, req):
    if "url" in req:
        return False
    if rule.get("namespaces") and req.get("namespace", "") not in rule["namespaces"]:
        return False
    if not rule.get("resources"):
        return True
    resource, sub = req["resource"], req.get("subresource", "")
    combined = resource + "/" + sub if sub else resource
    for gr in rule["resources"]:
        if gr.get("group", "") != req.get("group", ""):
            continue
        if not gr.get("resources"):
            return True
        for res in gr["resources"]:
            if gr.get("resourceNames") and req.get("name") not in gr["resourceNames"]:
                continue
            if res == combined or res == "*":
                return True
            if sub and res.startswith("*/") and sub == res[2:]:
                return True
            if res.endswith("/*") and resource == res[:-2]:
                return True
    return False


def _matches_url(rule, req):
    if "url" not in req:
        return False
    for u in rule["nonResourceURLs"]:
        if u == "*" or u == req["url"] or (u.endswith("*") and req["url"].startswith(u[:-1])):
            return True
    return False


def level_for(policy, req):
    """req: dict(user, groups, verb, and either url or group/resource[/subresource]/namespace)."""
    for r in policy["rules"]:
        if r.get("users") and req.get("user") not in r["users"]:
            continue
        if r.get("userGroups") and not set(r["userGroups"]) & set(req.get("groups", [])):
            continue
        if r.get("verbs") and req["verb"] not in r["verbs"]:
            continue
        if r.get("namespaces") or r.get("resources"):
            if _matches_resource(r, req):
                return r["level"]
            continue
        if r.get("nonResourceURLs"):
            if _matches_url(r, req):
                return r["level"]
            continue
        return r["level"]
    return "None"


ADMIN = {"user": "system:admin", "groups": ["system:masters", "system:authenticated"]}
KUBELET = {"user": "system:node:node-b", "groups": ["system:nodes", "system:authenticated"]}
SA = {"user": "system:serviceaccount:argocd:argocd-application-controller",
      "groups": ["system:serviceaccounts", "system:serviceaccounts:argocd", "system:authenticated"]}


def R(who, verb, resource, group="", sub="", ns="default"):
    d = dict(who, verb=verb, group=group, resource=resource, namespace=ns)
    if sub:
        d["subresource"] = sub
    return d


def U(who, verb, url):
    return dict(who, verb=verb, url=url)


# (description, request, expected level)
EXPECT = [
    ("probe /readyz", U(ADMIN, "get", "/readyz"), "None"),
    ("scrape /metrics", U(SA, "get", "/metrics"), "None"),
    ("discovery /apis/apps/v1", U(ADMIN, "get", "/apis/apps/v1"), "None"),
    ("lease renewal", R(KUBELET, "update", "leases", "coordination.k8s.io", ns="kube-node-lease"), "None"),
    ("event create", R(SA, "create", "events"), "None"),
    ("events.k8s.io create", R(SA, "create", "events", "events.k8s.io"), "None"),
    ("kubelet node status", R(KUBELET, "patch", "nodes", sub="status", ns=""), "None"),
    ("kubelet pod status", R(KUBELET, "patch", "pods", sub="status"), "None"),
    ("tokenreview", R(KUBELET, "create", "tokenreviews", "authentication.k8s.io", ns=""), "None"),
    ("subjectaccessreview", R(KUBELET, "create", "subjectaccessreviews", "authorization.k8s.io", ns=""), "None"),
    ("kubectl auth can-i", R(ADMIN, "create", "selfsubjectaccessreviews", "authorization.k8s.io", ns=""), "None"),
    ("admin reads a secret", R(ADMIN, "get", "secrets"), "Metadata"),
    ("controller lists secrets", R(SA, "list", "secrets", ns=""), "Metadata"),
    ("controller watches secrets", R(SA, "watch", "secrets", ns=""), "Metadata"),
    ("admin creates a secret", R(ADMIN, "create", "secrets"), "Metadata"),
    ("admin patches a secret", R(ADMIN, "patch", "secrets"), "Metadata"),
    ("admin deletes a secret", R(ADMIN, "delete", "secrets"), "Metadata"),
    ("configmap update", R(SA, "update", "configmaps"), "Metadata"),
    ("configmap read", R(ADMIN, "get", "configmaps"), "Metadata"),
    ("service account token request", R(KUBELET, "create", "serviceaccounts", sub="token"), "Metadata"),
    ("sealedsecret create", R(ADMIN, "create", "sealedsecrets", "bitnami.com"), "Metadata"),
    ("kubectl exec (create)", R(ADMIN, "create", "pods", sub="exec", ns="aa-u-ana"), "Request"),
    ("kubectl exec (websocket get)", R(ADMIN, "get", "pods", sub="exec", ns="aa-u-ana"), "Request"),
    ("kubectl attach", R(ADMIN, "create", "pods", sub="attach"), "Request"),
    ("kubectl port-forward", R(ADMIN, "create", "pods", sub="portforward", ns="wazuh"), "Request"),
    ("service proxy", R(ADMIN, "get", "services", sub="proxy", ns="monitoring"), "Request"),
    ("node proxy", R(ADMIN, "get", "nodes", sub="proxy", ns=""), "Request"),
    ("kubectl debug", R(ADMIN, "patch", "pods", sub="ephemeralcontainers"), "Request"),
    ("kubectl logs", R(ADMIN, "get", "pods", sub="log", ns="aa-u-ana"), "Metadata"),
    ("clusterrolebinding create", R(ADMIN, "create", "clusterrolebindings", "rbac.authorization.k8s.io", ns=""), "Request"),
    ("role delete", R(SA, "delete", "roles", "rbac.authorization.k8s.io"), "Request"),
    ("clusterrole read", R(ADMIN, "get", "clusterroles", "rbac.authorization.k8s.io", ns=""), "None"),
    ("VAP patch", R(ADMIN, "patch", "validatingadmissionpolicies", "admissionregistration.k8s.io", ns=""), "Request"),
    ("VAP binding delete", R(SA, "delete", "validatingadmissionpolicybindings", "admissionregistration.k8s.io", ns=""), "Request"),
    ("networkpolicy create", R(SA, "create", "networkpolicies", "networking.k8s.io", ns="aa-u-ana"), "Request"),
    ("ingress create (other networking)", R(SA, "create", "ingresses", "networking.k8s.io"), "Metadata"),
    ("admin cordons a node", R(ADMIN, "patch", "nodes", ns=""), "Metadata"),
    ("pod create", R(SA, "create", "pods"), "Metadata"),
    ("pod delete", R(ADMIN, "delete", "pods", ns="aa-u-ana"), "Metadata"),
    ("namespace delete", R(ADMIN, "delete", "namespaces", ns=""), "Metadata"),
    ("statefulset scale", R(ADMIN, "patch", "statefulsets", "apps", sub="scale", ns="aa-u-ana"), "Metadata"),
    ("argo application patch", R(SA, "patch", "applications", "argoproj.io", ns="argocd"), "Metadata"),
    ("pod list", R(ADMIN, "list", "pods", ns=""), "None"),
    ("deployment get", R(SA, "get", "deployments", "apps"), "None"),
]

SECRET_LIKE = [("", "secrets", ""), ("", "configmaps", ""), ("", "serviceaccounts", "token"),
               ("authentication.k8s.io", "tokenreviews", "")]
VERBS = ["get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"]


def check(policy, quiet=False):
    failures = 0
    for desc, req, want in EXPECT:
        got = level_for(policy, req)
        ok = got == want
        failures += not ok
        if not quiet or not ok:
            print("  %s %-34s %-9s (want %s)" % ("ok  " if ok else "FAIL", desc, got, want))
    for who in (ADMIN, KUBELET, SA, {"user": "system:anonymous", "groups": ["system:unauthenticated"]}):
        for group, res, sub in SECRET_LIKE:
            for verb in VERBS:
                got = level_for(policy, R(who, verb, res, group, sub))
                if LEVEL_RANK[got] > LEVEL_RANK["Metadata"]:
                    failures += 1
                    print("  FAIL invariant: %s %s %s%s by %s is logged at %s" % (
                        verb, group or "core", res, "/" + sub if sub else "", who["user"], got))
    return failures


def main(argv):
    quiet = "--quiet" in argv
    args = [a for a in argv if a != "--quiet"]
    if len(args) != 1:
        print(__doc__.split("\n")[2], file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            policy = yaml.safe_load(f)
        validate(policy)
    except (OSError, yaml.YAMLError, Invalid) as e:
        print("audit policy INVALID: %s" % e)
        return 2
    failures = check(policy, quiet)
    print("audit policy %s: %d rules, %d expectations, %d failure(s)" % (
        args[0], len(policy["rules"]), len(EXPECT), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
