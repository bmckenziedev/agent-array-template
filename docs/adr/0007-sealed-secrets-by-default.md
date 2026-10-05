# 0007: Sealed secrets by default

## Context

GitOps requires credential references without plaintext in Git, rendered configuration or examples.

## Decision

Use named Secret recipes and reviewed sealed ciphertext for bootstrap by default. Keep provider/master/upstream credentials in dedicated service scopes, never interactive sessions. Protect sealing private keys and recovery exports independently.

## Consequences

Sealing is not live etcd encryption, DLP or protection from cluster-root. Key rotation and restore drills are required. Logins are private authentication state and are never sealed, copied or backed up.
