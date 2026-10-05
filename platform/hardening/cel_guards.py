"""Conservative optional-field guard lint, supplemented by actual CEL evaluation.

This is a structural check, not an apiserver CEL type checker. Required schema
fields are excluded; optional accesses need a preceding has() in the expression.
"""
import re

OPTIONAL = {
    "namespace", "type", "externalIPs", "initContainers", "ephemeralContainers",
    "ports", "hostPort", "securityContext", "privileged", "procMount",
    "capabilities", "add", "hostIPC", "nodeName", "hostPID", "hostNetwork",
    "volumes", "hostPath", "path", "serviceAccountName", "ownerReferences",
    "controller", "automountServiceAccountToken", "runAsUser", "runAsGroup",
    "runAsNonRoot", "fsGroup", "supplementalGroups", "command",
    "readOnlyRootFilesystem", "volumeMounts", "readOnly", "mountPropagation",
    "seccompProfile", "localhostProfile",
}
ACCESS = re.compile(r"\b(?:object|oldObject|variables|c|p|v|m|o)(?:\.[A-Za-z][A-Za-z0-9_]*)+")


def check_expression(expression: str) -> list[str]:
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "tools/ci/lint_cel.py"
    spec = importlib.util.spec_from_file_location("shared_cel_lint", path)
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    return ["unguarded optional field: " + path for path in shared.reads(expression)]


def check_policy(policy: dict) -> list[str]:
    if policy.get("kind") != "ValidatingAdmissionPolicy":
        return []
    return [error for section in ("matchConditions", "variables", "validations")
            for item in policy["spec"].get(section, [])
            for error in check_expression(item["expression"])]
