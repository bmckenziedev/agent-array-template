#!/usr/bin/env python3
"""Conservative optional-field guard lint for admission CEL expressions."""
import argparse
import re
from pathlib import Path
import yaml

OPTIONAL = set("initContainers ephemeralContainers securityContext volumes volumeMounts ports env envFrom "
               "nodeSelector affinity tolerations hostNetwork hostPID hostIPC serviceAccountName "
               "automountServiceAccountToken runtimeClassName projected".split())
OPTIONAL |= set("annotations groups value args command subPath subPathExpr readOnly template persistentVolumeClaim ownerReferences nodeAffinity claimRef capabilities readOnlyRootFilesystem runAsNonRoot runAsUser runAsGroup fsGroup seccompProfile lifecycle livenessProbe readinessProbe startupProbe storageClassName local persistentVolumeReclaimPolicy secret configMap secretName serviceAccountToken sources audience expirationSeconds hostPath hostPort externalIPs nodeName privileged procMount add mountPropagation localhostProfile controller supplementalGroups namespace".split())
# PodTemplateSpec is required by every matched workload schema.
OPTIONAL.discard("template")
PATH = re.compile(r"\b[A-Za-z_]\w*(?:(?:\?\.|\.)[A-Za-z_]\w*)+")

def reads(expression, variables=None):
    variables = variables or {}
    # String constants do not read fields. Preserve positions for diagnostics.
    expression = re.sub(r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""", '""', expression)
    guards = set(re.findall(r"\bhas\s*\(\s*([\w.]+)\s*\)", expression))
    pending = re.findall(r"\bvariables\.(\w+)\b", expression)
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        value = variables.get(name, "")
        guards.update(re.findall(r"\bhas\s*\(\s*([\w.]+)\s*\)", value))
        pending.extend(re.findall(r"\bvariables\.(\w+)\b", value))
    missing = set()
    for match in PATH.finditer(expression):
        path = match.group()
        parts = re.split(r"\??\.", path)
        for index, field in enumerate(parts[1:], 1):
            if field == "namespace" and parts[0] == "request":
                continue
            if field not in OPTIONAL:
                continue
            prefix = '.'.join(parts[:index + 1])
            # has() is a presence check rather than an unsafe dereference.
            before = expression[:match.start()]
            is_has = bool(re.search(r"\bhas\s*\(\s*$", before))
            optional = "?." in path[:len(prefix) + 1]
            or_value = bool(re.match(r"\s*\.orValue\s*\(", expression[match.end():])) or ".orValue" in path
            if prefix not in guards and not (is_has and index == len(parts) - 1) and not optional and not or_value:
                missing.add(prefix)
    return sorted(missing)

def check(root):
    findings = []
    files = sorted(set(root.rglob("*.yaml")) | set(root.rglob("*.yml")))
    for file in files:
        for doc in yaml.safe_load_all(file.read_text(encoding="utf-8")):
            if not isinstance(doc, dict) or doc.get("kind") != "ValidatingAdmissionPolicy":
                continue
            spec = doc.get("spec", {})
            variables = {v["name"]: v["expression"] for v in spec.get("variables", [])}
            for section in ("validations", "matchConditions", "variables", "auditAnnotations"):
                for index, item in enumerate(spec.get(section, [])):
                    expression = item.get("expression", item.get("valueExpression", ""))
                    for path in reads(expression, variables):
                        findings.append((file.relative_to(root).as_posix(), section, index, path))
    return findings

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        parser.error("directory does not exist")
    findings = check(args.directory)
    for file, section, index, path in findings:
        print(f"FAIL {file}:{section}[{index}] unguarded {path}")
    print(f"cel: {len(findings)} fail")
    return int(bool(findings))

if __name__ == "__main__":
    raise SystemExit(main())
