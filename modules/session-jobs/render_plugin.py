"""Render the template ConfigMap and the selected sandbox runtime consistently."""
import json
import pathlib
import re

PLUGIN_NAME = "session-jobs"
ROOT = pathlib.Path(__file__).parent
KEY_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")


def subst(text, keys):
    def replace(match):
        name = match.group(1)
        if name not in keys:
            raise KeyError(f"unknown placeholder {name}")
        return keys[name]
    return KEY_RE.sub(replace, text)


def resolved_keys(model):
    settings = model["org"].get("modules", {}).get("session-jobs", {})
    runtime = settings.get("runtime", "kata")
    if runtime not in ("kata", "gvisor"):
        raise ValueError("session-jobs.runtime must be kata or gvisor")
    if runtime == "gvisor" and not model["org"].get("modules", {}).get("seccomp-gvisor", {}).get("enabled"):
        raise ValueError("gvisor requires modules.seccomp-gvisor.enabled")
    keys = dict(model["keys"])
    keys["M_SESSION_JOBS_RUNTIME"] = keys["RUNTIME_CLASS_VM" if runtime == "kata" else "RUNTIME_CLASS_GVISOR"]
    return runtime, keys


def render(model, emit):
    if not model["org"].get("modules", {}).get("session-jobs", {}).get("enabled", False):
        return
    runtime, keys = resolved_keys(model)
    template = subst((ROOT / "task-job.template.yaml").read_text(), keys)
    if runtime == "gvisor":
        template = template.replace("type: RuntimeDefault", "type: Localhost\n        localhostProfile: profiles/agent-array/runtime-default-clone3-enosys.json")
    policy = subst((ROOT / "admission-policy.yaml").read_text(), keys)
    if runtime == "gvisor":
        policy = re.sub(r"variables\.podSpec\.securityContext\.seccompProfile\.type\s*==\s*'RuntimeDefault'",
            "variables.podSpec.securityContext.seccompProfile.type == 'Localhost' "
            "&& has(variables.podSpec.securityContext.seccompProfile.localhostProfile) "
            "&& variables.podSpec.securityContext.seccompProfile.localhostProfile == "
            "'profiles/agent-array/runtime-default-clone3-enosys.json'", policy)
    emit("global/modules/session-jobs/k8s/admission-policy.yaml", policy)
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "session-job-template", "namespace": keys["NS_PORTAL"],
        "labels": {"app.kubernetes.io/part-of": keys["PROJECT_NAME"]}},
        "data": {"task-job.template.yaml": template}}
    emit("global/modules/session-jobs/k8s/job-template.yaml", json.dumps(cm, indent=2, sort_keys=True) + "\n")
