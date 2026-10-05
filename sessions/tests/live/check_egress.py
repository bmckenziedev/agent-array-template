#!/usr/bin/env python3
"""Operator-only anonymous connection probes through the estate container."""

import argparse
import ipaddress
import json
import subprocess
from pathlib import Path

import yaml

PROBE = """import json,socket,ssl,sys,urllib.parse
allow,deny,proxy=json.loads(sys.argv[1]); results=[]
def tunnel(host,allowed):
    endpoint=urllib.parse.urlparse(proxy)
    with socket.create_connection((endpoint.hostname,endpoint.port or 3128),timeout=5) as stream:
        stream.sendall(("CONNECT %s:443 HTTP/1.1\\r\\nHost: %s:443\\r\\n\\r\\n" % (host,host)).encode())
        response=b""
        while b"\\r\\n\\r\\n" not in response and len(response)<8192:
            chunk=stream.recv(1024)
            if not chunk: break
            response+=chunk
        accepted=response.startswith(b"HTTP/1.1 200") or response.startswith(b"HTTP/1.0 200")
        if accepted and allowed:
            with ssl.create_default_context().wrap_socket(stream,server_hostname=host): pass
        return accepted
if proxy:
    for host,allowed in [(h,True) for h in allow]+[("example.org",False),("api.kimi.ai.example.org",False),("evil.kimi.ai",False)]:
        try: opened=tunnel(host,allowed)
        except OSError: opened=False
        results.append({'host':host,'port':443,'proxy':True,'pass':opened==allowed})
    allow=[]
for host,port,allowed in [(h,443,True) for h in allow]+[(h,p,False) for h,p in deny]:
    try:
        with socket.create_connection((host,port),timeout=4) as connection:
            if allowed:
                with ssl.create_default_context().wrap_socket(connection,server_hostname=host): pass
        opened=True
    except OSError: opened=False
    results.append({'host':host,'port':port,'pass':opened==allowed})
print(json.dumps(results))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--rendered", type=Path, required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    objects = []
    for path in (args.rendered / "users" / args.user).rglob("*.yaml"):
        objects.extend(
            o for o in yaml.safe_load_all(path.read_text()) if isinstance(o, dict)
        )
    namespace = next(o["metadata"]["name"] for o in objects if o["kind"] == "Namespace")
    deny = [("169.254.169.254", 80), ("example.org", 80)]
    for obj in objects:
        if obj["kind"] == "NetworkPolicy":
            for rule in obj["spec"].get("egress", []):
                for peer in rule.get("to", []):
                    for cidr in peer.get("ipBlock", {}).get("except", []):
                        network = ipaddress.ip_network(cidr)
                        host = str(
                            network.network_address
                            + (1 if network.num_addresses > 1 else 0)
                        )
                        deny.extend([(host, 443), (host, 6443), (host, 10250)])
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "-n", namespace]
    result = subprocess.run(
        kube + ["get", "pods", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    pods = json.loads(result.stdout)["items"]
    checked = 0
    for pod in pods:
        if pod.get("status", {}).get("phase") != "Running":
            continue
        tool = next(
            (
                value
                for key, value in pod["metadata"]["labels"].items()
                if key.endswith("/tool")
            ),
            None,
        )
        allow = {
            "claude": ["api.anthropic.com", "claude.ai"],
            "codex": ["chatgpt.com", "auth.openai.com"],
            "kimi": ["api.kimi.ai", "auth.kimi.ai"],
        }.get(tool, [])
        proxy = None
        if tool == "kimi":
            for container in pod["spec"]["containers"]:
                for entry in container.get("env", []):
                    if entry["name"] == "AA_KIMI_EGRESS_PROXY":
                        proxy = entry.get("value")
            if not proxy:
                raise SystemExit("Kimi exact-host gateway configuration missing")
        # Anonymous CONNECT and TLS/SNI handshakes carry no vendor credentials.
        probe = subprocess.run(
            kube
            + [
                "exec",
                "-i",
                pod["metadata"]["name"],
                "-c",
                "estate",
                "--",
                "python3",
                "-",
                json.dumps(
                    [
                        allow,
                        deny + ([(host, 443) for host in allow] if proxy else []),
                        proxy,
                    ]
                ),
            ],
            input=PROBE,
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        results = json.loads(probe.stdout)
        print(json.dumps({"pod": pod["metadata"]["name"], "results": results}))
        if not all(r["pass"] for r in results):
            return 1
        checked += 1
    if not checked:
        raise SystemExit("No running user session pods")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
