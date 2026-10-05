#!/usr/bin/env python3
"""Check rendered cross-component contracts without a cluster."""
import argparse
import fnmatch
import json
from pathlib import Path
import re

import yaml

CLUSTER_KINDS = {"Namespace", "ClusterRole", "ClusterRoleBinding", "StorageClass",
                 "RuntimeClass", "PriorityClass", "CustomResourceDefinition",
                 "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding",
                 "ValidatingWebhookConfiguration", "MutatingWebhookConfiguration",
                 "PersistentVolume", "ClusterIssuer"}
DOCKERFILES = ("sessions/claude/image/Dockerfile", "sessions/codex/image/Dockerfile",
               "sessions/kimi/image/Dockerfile", "portal/panel/Dockerfile",
               "modules/session-jobs/image/Dockerfile", "services/pace/Dockerfile",
               "services/farm-mcp/Dockerfile", "modules/factory/Dockerfile",
               "modules/hermes-ops-chat/image/Dockerfile")


def documents(tree):
    result = []
    for path in sorted(tree.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if isinstance(doc, dict) and "apiVersion" in doc and "kind" in doc:
                result.append((path.relative_to(tree).as_posix(), doc))
    return result


def match_labels(selector, labels):
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for expr in selector.get("matchExpressions", []):
        key, op, values = expr["key"], expr["operator"], expr.get("values", [])
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and labels.get(key) in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


def application_specs(docs, tree):
    specs = []
    for _, doc in docs:
        if doc["kind"] == "Application":
            specs.append(doc["spec"])
        elif doc["kind"] == "ApplicationSet":
            template = doc["spec"]["template"]["spec"]
            for generator in doc["spec"].get("generators", []):
                for entry in generator.get("git", {}).get("directories", []):
                    if entry.get("exclude"):
                        continue
                    pattern = entry["path"].removeprefix("rendered/")
                    for directory in sorted(tree.glob(pattern)):
                        if not directory.is_dir():
                            continue
                        text = json.dumps(template)
                        for token in ("{{path.basename}}", "{{.path.basename}}"):
                            text = text.replace(token, directory.name)
                        text = text.replace("{{.path.path}}", "rendered/" + directory.relative_to(tree).as_posix())
                        specs.append(json.loads(text))
    return specs


def source_covers(spec, path):
    for source in spec.get("sources", [spec.get("source", {})]):
        base = source.get("path", "").removeprefix("rendered/").rstrip("/")
        if not base or not path.startswith(base + "/"):
            continue
        relative = path[len(base) + 1:]
        options = source.get("directory", {})
        if "/" in relative and not options.get("recurse"):
            continue
        if not fnmatch.fnmatchcase(relative, options.get("include", "*.yaml")):
            continue
        if options.get("exclude") and fnmatch.fnmatchcase(relative, options["exclude"]):
            continue
        return True
    return False


def pod_specs(doc):
    if doc["kind"] == "Pod":
        return [doc["spec"]]
    if doc["kind"] == "CronJob":
        return [doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]]
    template = doc.get("spec", {}).get("template", {})
    return [template["spec"]] if "spec" in template else []


def check(tree, root):
    docs = documents(tree)
    errors = []
    projects = {d["metadata"]["name"]: d["spec"] for _, d in docs if d["kind"] == "AppProject"}
    apps = application_specs(docs, tree)
    services = [d for _, d in docs if d["kind"] == "Service"]
    configmaps = {(d.get("metadata", {}).get("namespace"), d.get("metadata", {}).get("name"))
                  for _, d in docs if d["kind"] == "ConfigMap"}
    shapes = "\n".join(json.dumps(d) for _, d in docs if d["kind"] == "ValidatingAdmissionPolicy"
                       and "shape" in d["metadata"]["name"])
    def fail(path, message):
        errors.append(f"{path}: {message}")
    for path, doc in docs:
        kind = doc["kind"]
        metadata = doc.get("metadata", {})
        namespace = metadata.get("namespace")
        if path.startswith("files/"):
            fail(path, "Kubernetes manifest under files/")
            continue
        covering = [app for app in apps if source_covers(app, path)]
        if len(covering) != 1:
            fail(path, f"manifest covered by {len(covering)} Applications; expected exactly one")
        else:
            app = covering[0]
            project = projects.get(app.get("project"))
            if project is None:
                fail(path, "Application references missing AppProject")
            else:
                cluster = kind in CLUSTER_KINDS
                rules = project.get("clusterResourceWhitelist" if cluster else "namespaceResourceWhitelist", [])
                group = doc["apiVersion"].split("/")[0] if "/" in doc["apiVersion"] else ""
                if not any(fnmatch.fnmatchcase(group, r["group"]) and fnmatch.fnmatchcase(kind, r["kind"])
                           for r in rules):
                    fail(path, f"{kind} not permitted by AppProject {app['project']}")
                if not cluster:
                    target = namespace or app.get("destination", {}).get("namespace", "")
                    if not any(fnmatch.fnmatchcase(target, d.get("namespace", ""))
                               and fnmatch.fnmatchcase(app.get("destination", {}).get("server", ""), d.get("server", "*"))
                               for d in project.get("destinations", [])):
                        fail(path, f"namespace {target} not permitted by AppProject {app['project']}")
        if kind == "ServiceMonitor":
            spec = doc["spec"]
            ns = spec.get("namespaceSelector", {})
            matching = [s for s in services if (ns.get("any") or s["metadata"].get("namespace") in
                        ns.get("matchNames", [namespace])) and
                        match_labels(spec.get("selector", {}), s["metadata"].get("labels", {}))]
            for endpoint in spec.get("endpoints", []):
                port = endpoint.get("port")
                if port and not any(any(p.get("name") == port for p in s["spec"].get("ports", [])) for s in matching):
                    fail(path, f"ServiceMonitor port {port} absent on matching Services")
        if kind == "NetworkPolicy":
            for rule in doc["spec"].get("egress", []):
                if not any(p.get("port") == 53 for p in rule.get("ports", [])):
                    continue
                peers = rule.get("to", [])
                if not any(p.get("namespaceSelector", {}).get("matchLabels", {}).get(
                        "kubernetes.io/metadata.name") == "kube-system" and
                        p.get("podSelector", {}).get("matchLabels", {}).get("k8s-app") == "kube-dns" for p in peers):
                    fail(path, "DNS egress lacks the CoreDNS namespace and pod selectors")
        if kind in ("Role", "ClusterRole"):
            for rule in doc.get("rules", []):
                if "services/proxy" in rule.get("resources", []):
                    for name in rule.get("resourceNames", []):
                        if ":" not in name:
                            fail(path, "services/proxy resourceName lacks the documented port")
        for pod in pod_specs(doc):
            if not path.startswith("users/") or "sessions" not in path:
                continue
            volumes = pod.get("volumes", [])
            for volume in volumes:
                producers = [volume["configMap"]] if "configMap" in volume else [
                    s["configMap"] for s in volume.get("projected", {}).get("sources", []) if "configMap" in s]
                for producer in producers:
                    name = producer["name"]
                    if name in ("claude-policy", "codex-policy", "kimi-policy", "claude-mcp", "codex-mcp",
                                "context-policy", "org-directory"):
                        if (namespace, name) not in configmaps:
                            fail(path, f"ConfigMap {namespace}/{name} has no producer")
            for container in pod.get("containers", []) + pod.get("initContainers", []):
                if container.get("image") and container["image"] not in shapes:
                    fail(path, f"container {container['name']} image absent from sessions shape policy")
        if kind == "ConfigMap" and metadata.get("name") in ("argocd-rbac-cm", "grafana-config", "oauth2-proxy-config", "hermes-ops-chat-config"):
            if re.search(r"oidc:", json.dumps(doc.get("data", {}))):
                fail(path, "non-Kubernetes consumer uses an oidc:-prefixed group")
    for filename in DOCKERFILES:
        if not (root / filename).is_file():
            fail(filename, "section-7 Dockerfile missing")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rendered", required=True, type=Path)
    parser.add_argument("--root", default=Path("."), type=Path)
    args = parser.parse_args(argv)
    if not args.rendered.is_dir():
        parser.error("rendered directory does not exist")
    try:
        errors = check(args.rendered, args.root)
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as exc:
        print(f"contracts: invalid render: {exc}")
        return 1
    for error in errors:
        print(error)
    print(f"contracts: {len(errors)} errors")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
