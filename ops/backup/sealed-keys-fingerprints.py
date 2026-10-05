#!/usr/bin/env python3
"""sealed-keys-fingerprints.py -- SHA-256 fingerprints of the sealed-secrets sealing certs.

Usage: sealed-keys-fingerprints.py [FILE | -]

Reads a kubectl YAML dump of the sealing-key Secrets (a List, or one Secret per document),
such as /var/backups/agent-array/kube/sealed-secrets-keys.yaml, or `kubectl -n kube-system
get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml` on stdin. For every
Secret it prints one line:

    <secret name>  <creationTimestamp>  <SHA-256 fingerprint of tls.crt (DER), colon-hex>

It reads ONLY the public certificate (`tls.crt`). `tls.key` is never decoded, printed or
copied, so the output is safe for logs, MANIFEST sidecars and alert annotations. The
fingerprint format equals `openssl x509 -noout -fingerprint -sha256`, so it can be compared
with the committed public cert (ops/sealed-secrets/pub-cert.pem) directly.
Exit 0 with at least one key printed, 1 if no key was found, 2 on bad input.
"""
import base64
import hashlib
import re
import sys

import yaml

PEM_RE = re.compile(rb"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)


def der_fingerprint(der: bytes) -> str:
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


def iter_secrets(docs):
    for doc in docs:
        if not doc:
            continue
        if doc.get("kind") == "List":
            yield from doc.get("items") or []
        elif doc.get("kind") == "Secret":
            yield doc


def main(argv):
    src = argv[1] if len(argv) > 1 else "-"
    try:
        text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        docs = list(yaml.safe_load_all(text))
    except (OSError, yaml.YAMLError):
        # Parser exceptions can quote credential-bearing lines from the input.
        print("ERROR: cannot parse controller key inventory", file=sys.stderr)
        return 2
    found = 0
    for secret in iter_secrets(docs):
        name = (secret.get("metadata") or {}).get("name", "?")
        created = (secret.get("metadata") or {}).get("creationTimestamp", "?")
        crt_b64 = (secret.get("data") or {}).get("tls.crt")
        if not crt_b64:
            print(f"{name}  {created}  NO-TLS-CRT", file=sys.stderr)
            continue
        pem = base64.b64decode(crt_b64)
        for match in PEM_RE.finditer(pem):
            der = base64.b64decode(b"".join(match.group(1).split()))
            print(f"{name}  {created}  {der_fingerprint(der)}")
            found += 1
    if found == 0:
        print("ERROR: no sealing certificate found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
