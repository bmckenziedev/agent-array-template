#!/usr/bin/env python3
"""Operator-only server dry-runs. Never applies or persists an object."""

import argparse
import copy
import json
import subprocess
from pathlib import Path

import yaml


def objects(root):
    for path in root.rglob("*.yaml"):
        yield from (
            obj for obj in yaml.safe_load_all(path.read_text()) if isinstance(obj, dict)
        )


def additional_denials(valid, sts, source):
    """Build valid-schema adversarial objects without contacting a cluster."""
    cases = []

    def pod_case(name, mutate, pod=valid):
        obj = copy.deepcopy(pod)
        mutate(obj)
        cases.append((name, obj, False))

    def cli(obj):
        return next(
            c
            for c in obj["spec"]["containers"]
            if c["name"] in {"claude", "codex", "kimi"}
        )

    def login_mount(obj):
        name = next(
            v["name"] for v in obj["spec"]["volumes"] if "persistentVolumeClaim" in v
        )
        return {"name": name, "mountPath": "/forbidden-login"}

    def token(obj):
        return next(
            p["serviceAccountToken"]
            for v in obj["spec"]["volumes"]
            if "projected" in v
            for p in v["projected"]["sources"]
            if "serviceAccountToken" in p
        )

    for name, field, value in [
        ("privileged", "privileged", True),
        ("privilege escalation", "allowPrivilegeEscalation", True),
        ("writable root", "readOnlyRootFilesystem", False),
    ]:
        pod_case(
            name,
            lambda obj, field=field, value=value: cli(obj)
            .setdefault("securityContext", {})
            .update({field: value}),
        )
    pod_case(
        "hostPath",
        lambda obj: obj["spec"]["volumes"].append(
            {"name": "forbidden-host", "hostPath": {"path": "/", "type": "Directory"}}
        ),
    )
    pod_case(
        "automounted API token",
        lambda obj: obj["spec"].update(automountServiceAccountToken=True),
    )
    pod_case(
        "wrong service account",
        lambda obj: obj["spec"].update(serviceAccountName="default"),
    )
    pod_case(
        "API token audience",
        lambda obj: token(obj).update(audience="https://kubernetes.default.svc"),
    )
    pod_case(
        "excessive token TTL", lambda obj: token(obj).update(expirationSeconds=3601)
    )
    pod_case(
        "estate login mount",
        lambda obj: next(c for c in obj["spec"]["containers"] if c["name"] == "estate")
        .setdefault("volumeMounts", [])
        .append(login_mount(obj)),
    )
    pod_case(
        "init login mount",
        lambda obj: obj["spec"]["initContainers"][0]
        .setdefault("volumeMounts", [])
        .append(login_mount(obj)),
    )
    pod_case(
        "wrong tool claim",
        lambda obj: next(
            v["persistentVolumeClaim"]
            for v in obj["spec"]["volumes"]
            if "persistentVolumeClaim" in v
        ).update(
            claimName=(
                "kimi-home-other-node"
                if cli(obj)["name"] != "kimi"
                else "codex-home-other-node"
            )
        ),
    )
    pod_case(
        "omitted RAM medium",
        lambda obj: next(
            v["emptyDir"]
            for v in obj["spec"]["volumes"]
            if "emptyDir" in v and v["emptyDir"].get("medium") == "Memory"
        ).pop("medium"),
    )
    pod_case(
        "command override",
        lambda obj: cli(obj).update(command=["/bin/sh", "-c", "sleep 3600"]),
    )
    if any(c["name"] == "supervisor" for c in valid["spec"]["containers"]):
        def supervisor(obj):
            return next(c for c in obj["spec"]["containers"] if c["name"] == "supervisor")
        pod_case("supervisor wrong image", lambda obj: supervisor(obj).update(image="example.invalid/unapproved@sha256:" + "a" * 64))
        pod_case("supervisor mismatched pod gid", lambda obj: obj["spec"]["securityContext"].update(runAsGroup=1001))
        pod_case("supervisor default container", lambda obj: obj["spec"]["containers"].insert(0, obj["spec"]["containers"].pop(-1)))
        pod_case("supervisor mismatched uid", lambda obj: supervisor(obj)["securityContext"].update(runAsUser=1001))
        pod_case("shared PID namespace", lambda obj: obj["spec"].update(shareProcessNamespace=True))
        pod_case("supervisor added capability", lambda obj: supervisor(obj)["securityContext"]["capabilities"].update(add=["SYS_PTRACE"]))
        pod_case("CLI private control mount", lambda obj: cli(obj)["volumeMounts"].append({"name": "supervisor-run", "mountPath": "/control"}))
        pod_case("CLI supervisor token mount", lambda obj: cli(obj)["volumeMounts"].append({"name": "supervisor-token", "mountPath": "/token", "readOnly": True}))
        pod_case("supervisor full login mount", lambda obj: next(m for m in supervisor(obj)["volumeMounts"] if m["name"] == "login").pop("subPath"))
        pod_case("supervisor writable transcript", lambda obj: next(m for m in supervisor(obj)["volumeMounts"] if m["name"] == "login").update(readOnly=False))
        pod_case("supervisor TCP listener", lambda obj: supervisor(obj).update(ports=[{"containerPort": 8080}]))
    namespace = valid["metadata"]["namespace"]
    bad_sts = copy.deepcopy(sts)
    bad_sts["metadata"]["name"] = "unapproved-session-name"
    cases.append(("bad StatefulSet name", bad_sts, False))
    for item in source:
        if item["kind"] == "StatefulSet":
            template = item["spec"]["template"]
            if any(c["name"] == "usage" for c in template["spec"]["containers"]):
                pod = {
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        **copy.deepcopy(template["metadata"]),
                        "name": "admission-usage-negative",
                        "namespace": namespace,
                        "ownerReferences": [
                            {
                                "apiVersion": "apps/v1",
                                "kind": "StatefulSet",
                                "name": item["metadata"]["name"],
                                "uid": valid["metadata"]["ownerReferences"][0]["uid"],
                                "controller": True,
                            }
                        ],
                    },
                    "spec": copy.deepcopy(template["spec"]),
                }

                def full_home(obj):
                    usage = next(
                        c for c in obj["spec"]["containers"] if c["name"] == "usage"
                    )
                    for mount in usage["volumeMounts"]:
                        if mount.get("subPath") == "sessions":
                            mount.pop("subPath")

                pod_case("usage full home mount", full_home, pod)
        if item["kind"] == "PersistentVolumeClaim":
            for name, mutate in [
                (
                    "wrong login storage class",
                    lambda obj: obj["spec"].update(storageClassName="unapproved-login"),
                ),
                (
                    "wrong PVC user label",
                    lambda obj: obj["metadata"]["labels"].update(
                        {
                            next(
                                k
                                for k in obj["metadata"]["labels"]
                                if k.endswith("/user")
                            ): "other-user"
                        }
                    ),
                ),
                (
                    "PVC clone dataSource",
                    lambda obj: obj["spec"].update(
                        dataSource={
                            "apiGroup": "",
                            "kind": "PersistentVolumeClaim",
                            "name": "other-login",
                        }
                    ),
                ),
            ]:
                obj = copy.deepcopy(item)
                obj["metadata"]["name"] += "-admission-negative"
                mutate(obj)
                cases.append((name, obj, False))
    pod_spec = {
        "restartPolicy": "Never",
        "containers": [{"name": "test", "image": "busybox:1.36", "command": ["true"]}],
    }
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "admission-negative", "namespace": namespace},
        "spec": {"template": {"spec": pod_spec}},
    }
    cron = {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": "admission-negative", "namespace": namespace},
        "spec": {"schedule": "0 0 * * *", "jobTemplate": {"spec": job["spec"]}},
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "admission-negative", "namespace": namespace},
        "spec": {
            "selector": {"app": "forbidden"},
            "ports": [{"port": 8080, "targetPort": 8080}],
        },
    }
    cases.extend([(obj["kind"], obj, False) for obj in (job, cron, service)])
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--rendered", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--non-session-namespace", default="default")
    parser.add_argument("--as", dest="as_user")
    parser.add_argument("--as-group", action="append", default=[])
    parser.add_argument(
        "--holder-subject", help="OIDC username for negative direct pod-write check"
    )
    parser.add_argument(
        "--unauthorised-subject", help="OIDC username for negative exec guard check"
    )
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig]
    if args.as_user:
        kube += ["--as", args.as_user]
    for group in args.as_group:
        kube += ["--as-group", group]
    source = list(objects(args.rendered / "users" / args.user))
    namespace = next(o["metadata"]["name"] for o in source if o["kind"] == "Namespace")
    sts = next(o for o in source if o["kind"] == "StatefulSet")
    live_sts = subprocess.run(
        kube
        + [
            "-n",
            namespace,
            "get",
            "statefulset",
            sts["metadata"]["name"],
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    sts_uid = json.loads(live_sts.stdout)["metadata"]["uid"]
    valid = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "admission-positive",
            "namespace": namespace,
            **sts["spec"]["template"]["metadata"],
        },
        "spec": sts["spec"]["template"]["spec"],
    }
    valid["metadata"]["ownerReferences"] = [
        {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "name": sts["metadata"]["name"],
            "uid": sts_uid,
            "controller": True,
        }
    ]
    plain = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "admission-positive",
            "namespace": args.non_session_namespace,
        },
        "spec": {"containers": [{"name": "test", "image": "busybox:1.36"}]},
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "admission-positive",
            "namespace": args.non_session_namespace,
        },
        "spec": {
            "selector": {"matchLabels": {"app": "test"}},
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": plain["spec"],
            },
        },
    }
    cases = [
        ("non-session plain Pod", plain, True),
        ("non-session Deployment", deployment, True),
        ("valid session Pod", valid, True),
    ]
    for name, mutate in [
        ("host networking", lambda o: o["spec"].update(hostNetwork=True)),
        (
            "unapproved image",
            lambda o: o["spec"]["containers"][0].update(image="busybox:1.36"),
        ),
        ("wrong runtime", lambda o: o["spec"].update(runtimeClassName="runc")),
        (
            "injected vendor key",
            lambda o: o["spec"]["containers"][0]
            .setdefault("env", [])
            .append({"name": "OPENAI_API_KEY", "value": "synthetic"}),
        ),
        (
            "ephemeral container",
            lambda o: o["spec"].update(
                ephemeralContainers=[{"name": "debug", "image": "busybox:1.36"}]
            ),
        ),
    ]:
        obj = copy.deepcopy(valid)
        mutate(obj)
        cases.append((name, obj, False))
    invalid_workload = copy.deepcopy(deployment)
    invalid_workload["metadata"]["namespace"] = namespace
    cases.append(("session Deployment", invalid_workload, False))
    cases.extend(additional_denials(valid, sts, source))
    for name, obj, admitted in cases:
        result = subprocess.run(
            kube + ["create", "--dry-run=server", "-f", "-", "-o", "json"],
            input=json.dumps(obj),
            capture_output=True,
            text=True,
            timeout=60,
        )
        passed = (result.returncode == 0) == admitted
        if not admitted and result.returncode != 0:
            # Missing resources, malformed schemas and connectivity failures do not prove enforcement.
            reason = result.stderr.lower()
            passed = "denied" in reason or "forbidden" in reason
        print(("PASS " if passed else "FAIL ") + name)
        if not passed:
            print(result.stderr)
            return 1
    if args.holder_subject:
        result = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                args.kubeconfig,
                "--as",
                args.holder_subject,
                "create",
                "--dry-run=server",
                "-f",
                "-",
            ],
            input=json.dumps(valid),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print("FAIL holder direct pod write admitted")
            return 1
        print("PASS holder direct pod write denied")
    if args.unauthorised_subject:
        current = subprocess.run(
            kube + ["-n", namespace, "get", "pods", "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        pods = json.loads(current.stdout)["items"]
        pod = next(p for p in pods if p.get("status", {}).get("phase") == "Running")
        cli = next(
            c["name"]
            for c in pod["spec"]["containers"]
            if c["name"] in {"claude", "codex", "kimi"}
        )
        # A harmless command exercises CONNECT; no login files or payloads are read.
        other = [
            "kubectl",
            "--kubeconfig",
            args.kubeconfig,
            "--as",
            args.unauthorised_subject,
            "-n",
            namespace,
        ]
        result = subprocess.run(
            other + ["exec", pod["metadata"]["name"], "-c", cli, "--", "true"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print("FAIL unauthorised exec admitted")
            return 1
        print(
            "PASS unauthorised exec denied; verify denial is from exec admission rather than RBAC"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
