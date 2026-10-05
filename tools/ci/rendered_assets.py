"""Recognize exact native configuration/strategic-merge assets, never directories.

These are consumed by workstation/API-server configuration or the reviewed Wazuh
assembler. They must never be applied as standalone Kubernetes resources.
"""

PATCHES = {
    "dashboard.yaml": ("apps/v1", "Deployment", {"wazuh-dashboard"}),
    "indexer.yaml": ("apps/v1", "StatefulSet", {"wazuh-indexer"}),
    "manager-master.yaml": ("apps/v1", "StatefulSet", {"wazuh-manager-master"}),
    "namespace.yaml": ("v1", "Namespace", {"wazuh"}),
    "services-clusterip.yaml": ("v1", "Service", {"wazuh", "wazuh-cluster", "wazuh-indexer", "indexer", "dashboard"}),
    "delete-demo-secrets.yaml": ("v1", "Secret", {"indexer-cred", "dashboard-cred", "wazuh-api-cred", "wazuh-authd-pass", "wazuh-cluster-key"}),
}


def is_non_manifest(path, doc):
    """Return true for a known valid asset; malformed known assets fail closed."""
    path = str(path).replace("\\", "/")
    overlay, _, filename = path.rpartition('/')
    known = (path in {'files/cluster/oidc/kubeconfig-oidc.yaml',
                     'files/ops/audit/audit-policy.yaml'} or
             (overlay == 'files/modules/wazuh/overlay' and
              filename in {*PATCHES, 'delete-workers.yaml', 'kustomization.yml'}))
    if not isinstance(doc, dict):
        if known:
            raise ValueError('Malformed native configuration or overlay fragment: ' + path)
        return False
    if path == "files/cluster/oidc/kubeconfig-oidc.yaml":
        valid = (doc.get("apiVersion") == "v1" and doc.get("kind") == "Config"
                 and all(key in doc for key in ("clusters", "contexts", "users", "current-context"))
                 and "metadata" not in doc)
    elif path == "files/ops/audit/audit-policy.yaml":
        valid = (doc.get("apiVersion") == "audit.k8s.io/v1" and doc.get("kind") == "Policy"
                 and isinstance(doc.get("rules"), list) and "metadata" not in doc
                 and "ResponseStarted" not in doc.get("omitStages", []))
    elif overlay == "files/modules/wazuh/overlay":
        name = path.rsplit("/", 1)[-1]
        if name == "delete-workers.yaml":
            valid = ((doc.get("apiVersion"), doc.get("kind"), doc.get("metadata", {}).get("name"))
                     in {("apps/v1", "StatefulSet", "wazuh-manager-worker"), ("v1", "Service", "wazuh-workers")}
                     and doc.get("$patch") == "delete")
        elif name in PATCHES:
            version, kind, names = PATCHES[name]
            valid = (doc.get("apiVersion") == version and doc.get("kind") == kind
                     and doc.get("metadata", {}).get("name") in names)
            if name.startswith("delete-"):
                valid = valid and doc.get("$patch") == "delete" and not any(key in doc for key in ("data", "stringData"))
            elif kind in ("Deployment", "StatefulSet"):
                valid = valid and "selector" not in doc.get("spec", {}) and "template" in doc.get("spec", {})
            elif kind == "Service":
                valid = valid and "selector" not in doc.get("spec", {}) and (
                    doc.get("spec", {}).get("type") == "ClusterIP" or
                    (doc["metadata"]["name"] in {"wazuh-cluster", "wazuh-indexer"}
                     and doc.get("spec") == {"publishNotReadyAddresses": True}))
            elif kind == "Namespace":
                valid = valid and doc.get("metadata", {}).get("annotations", {}).get("argocd.argoproj.io/sync-options") == "Prune=false,Delete=false"
        elif name == "kustomization.yml":
            valid = (doc.get("apiVersion") == "kustomize.config.k8s.io/v1beta1" and doc.get("kind") == "Kustomization"
                     and doc.get("resources") == ["../../wazuh", "networkpolicy.yaml"]
                     and isinstance(doc.get("patches"), list) and isinstance(doc.get("images"), list))
        else:
            return False
    else:
        return False
    if not valid:
        raise ValueError("Malformed native configuration or overlay fragment: " + path)
    return True
