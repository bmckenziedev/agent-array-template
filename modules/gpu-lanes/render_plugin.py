"""Render optional GPU lanes using local PV topology, without hostname scheduling."""
import json
import re

PLUGIN_NAME = "gpu-lanes"
IMAGE = ("ghcr.io/ggml-org/llama.cpp:server-cuda12-b11382@sha256:"
         "ef08b5a98b1170f2b62177be0a4027c88a84043c55afbf190dd18b9e7cdcfebf")
DOWNLOAD_IMAGE = ("python:3.12-alpine@sha256:"
                  "1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4")
SECURITY = {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]}}

# Atomic replacement prevents an interrupted download from being used as weights.
DOWNLOAD = """import hashlib, os, pathlib, urllib.request, uuid
p = pathlib.Path('/cache/' + os.environ['MODEL_SHA256'] + '.gguf')
def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()
if not p.exists() or digest(p) != os.environ['MODEL_SHA256']:
    temp = p.with_name(p.name + '.' + uuid.uuid4().hex + '.partial')
    with urllib.request.urlopen(os.environ['MODEL_URL'], timeout=120) as r, temp.open('wb') as f:
        while True:
            block = r.read(1048576)
            if not block:
                break
            f.write(block)
    if digest(temp) != os.environ['MODEL_SHA256']:
        temp.unlink()
        raise SystemExit('model checksum mismatch')
    temp.replace(p)
"""


def render(model, emit):
    options = model["org"].get("modules", {}).get("gpu-lanes", {})
    if not options.get("enabled"):
        return
    keys = model["keys"]
    namespace, prefix = keys["NS_MODELS"], keys["LABEL_PREFIX"]
    nodes = {n["name"]: n for n in model["org"]["nodes"] if "gpu" in n["roles"]}
    objects = []
    def add(kind, name, spec, api="v1", ns=namespace):
        metadata = {"name": name, "labels": {"app.kubernetes.io/name": name,
                    "app.kubernetes.io/part-of": keys["PROJECT_NAME"],
                    "app.kubernetes.io/component": "gpu-lanes"}}
        if ns:
            metadata["namespace"] = ns
        objects.append({"apiVersion": api, "kind": kind, "metadata": metadata, "spec": spec})
    storage_class = keys["PROJECT_NAME"] + "-gpu-cache"
    add("StorageClass", storage_class, {}, "storage.k8s.io/v1", None)
    objects[-1].pop("spec")
    objects[-1].update({"provisioner": "kubernetes.io/no-provisioner",
                       "volumeBindingMode": "WaitForFirstConsumer"})
    for name, node in sorted(nodes.items()):
        add("PersistentVolume", "model-cache-" + name, {
            "capacity": {"storage": options.get("cache_size", "100Gi")},
            "volumeMode": "Filesystem", "accessModes": ["ReadWriteOnce"],
            "persistentVolumeReclaimPolicy": "Retain", "storageClassName": storage_class,
            "local": {"path": options.get("cache_root", "/var/lib/model-cache")},
            "nodeAffinity": {"required": {"nodeSelectorTerms": [{"matchExpressions": [
                {"key": "kubernetes.io/hostname", "operator": "In", "values": [name]}]}]}}}, ns=None)
        add("PersistentVolumeClaim", "model-cache-" + name, {
            "accessModes": ["ReadWriteOnce"], "storageClassName": storage_class,
            "volumeName": "model-cache-" + name,
            "resources": {"requests": {"storage": options.get("cache_size", "100Gi")}}})
    names = set()
    for lane in sorted(options.get("lanes", []), key=lambda l: l["name"]):
        name, node, sha = lane["name"], lane["node"], lane["gguf_sha256"]
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name) or name in names:
            raise ValueError("Invalid or duplicate lane name")
        names.add(name)
        if node not in nodes or not re.fullmatch(r"[0-9a-f]{64}", sha) or sha == "0" * 64:
            raise ValueError("Lane needs a GPU node and a verified nonzero SHA256")
        if not lane["gguf_url"].startswith("https://"):
            raise ValueError("Model download requires HTTPS")
        if type(lane["slots"]) is not int or lane["slots"] < 1 or lane["ctx"] < 1:
            raise ValueError("Invalid context/slots")
        extra = lane.get("extra_args", [])
        reserved = ("--api-key", "--api-key-file", "--host", "--port", "--model", "--alias", "--parallel")
        if not isinstance(extra, list) or any(not isinstance(a, str) for a in extra):
            raise ValueError("extra_args must be a list of strings")
        if any(a in ("-m", "-a", "-np") or a.split("=", 1)[0] in reserved for a in extra):
            raise ValueError("extra_args cannot override lane identity/authentication")
        labels = {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": keys["PROJECT_NAME"],
                  "app.kubernetes.io/component": "gpu-lanes", prefix + "/model-lane": "true"}
        mounts = [{"name": "cache", "mountPath": "/cache", "readOnly": True},
                  {"name": "auth", "mountPath": "/run/model-auth", "readOnly": True},
                  {"name": "tmp", "mountPath": "/tmp"}]
        init = {"name": "download-model", "image": options.get("download_image", DOWNLOAD_IMAGE),
                "command": ["python3", "-c", DOWNLOAD], "securityContext": SECURITY,
                "env": [{"name": "MODEL_URL", "value": lane["gguf_url"]},
                        {"name": "MODEL_SHA256", "value": sha}],
                "volumeMounts": [{"name": "cache", "mountPath": "/cache"},
                                 {"name": "tmp", "mountPath": "/tmp"}]}
        if (not re.search(r"@sha256:[0-9a-f]{64}$", init["image"])
                or init["image"].endswith("@sha256:" + "0" * 64)):
            raise ValueError("Configure a digest-pinned gpu-lanes.download_image")
        pod = {"automountServiceAccountToken": False,
               "runtimeClassName": keys["RUNTIME_CLASS_GPU"],
               "nodeSelector": {prefix + "/role-gpu": "true"},
               "tolerations": [{"key": prefix + "/gpu", "operator": "Equal", "value": "true", "effect": "NoSchedule"}],
               "securityContext": {"fsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
               "initContainers": [init],
               "containers": [{"name": "server", "image": IMAGE, "securityContext": SECURITY,
                   "args": ["-m", "/cache/" + sha + ".gguf", "-a", lane["model_group"],
                            "--host", "0.0.0.0", "--port", "8080", "--api-key-file",
                            "/run/model-auth/api-key", "-c", str(lane["ctx"]),
                            "-np", str(lane["slots"]), "--metrics", "--no-webui"] + extra,
                   "ports": [{"name": "http", "containerPort": 8080}],
                   "resources": {"requests": {"cpu": "1", "memory": "2Gi", "nvidia.com/gpu": 1},
                                 "limits": {"memory": "32Gi", "nvidia.com/gpu": 1}},
                   "readinessProbe": {"httpGet": {"path": "/health", "port": 8080}},
                   "volumeMounts": mounts}],
               "volumes": [{"name": "cache", "persistentVolumeClaim": {"claimName": "model-cache-" + node}},
                           {"name": "auth", "secret": {"secretName": "model-server-auth"}},
                           {"name": "tmp", "emptyDir": {}}]}
        add("Deployment", name, {"replicas": 1, "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {"metadata": {"labels": labels}, "spec": pod}}, "apps/v1")
        add("Service", name, {"type": "ClusterIP", "selector": {"app.kubernetes.io/name": name},
                              "ports": [{"name": "http", "port": 8080, "targetPort": 8080}]})
        objects[-1]["metadata"]["labels"].update(labels)
    sources = [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": keys["NS_LLM"]}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "litellm"}}}]
    if model["org"].get("modules", {}).get("factory", {}).get("enabled"):
        sources.append({"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": keys["NS_FACTORY"]}}})
    add("NetworkPolicy", "model-lanes-allow", {"podSelector": {"matchLabels": {prefix + "/model-lane": "true"}},
        "policyTypes": ["Ingress", "Egress"], "ingress": [{"from": sources, "ports": [{"protocol": "TCP", "port": 8080}]}],
        "egress": [{"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}, "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
                    "ports": [{"port": 53, "protocol": p} for p in ("UDP", "TCP")]},
                   {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": json.loads(keys["PRIVATE_CIDRS_JSON"])}}],
                    "ports": [{"port": 443, "protocol": "TCP"}]}]}, "networking.k8s.io/v1")
    # Scraping directly would violate factory-only serving ingress. Prometheus must
    # use a permitted proxy; this monitor is opt-in and does not widen ingress.
    add("ServiceMonitor", "model-lanes", {"selector": {"matchLabels": {prefix + "/model-lane": "true"}},
        "endpoints": [{"port": "http", "path": "/metrics", "authorization": {
            "credentials": {"name": "model-server-auth", "key": "api-key"}}}]}, "monitoring.coreos.com/v1")
    emit("global/modules/gpu-lanes/k8s/lanes.yaml", "\n---\n".join(
        json.dumps(o, sort_keys=True, indent=2) for o in objects) + "\n")
