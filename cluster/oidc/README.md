# OIDC identity

OIDC gives each API request an attributable subject and group membership, replacing shared daily admin certificates.

## Interface

The k3s drop-in appends API-server flags using `kube-apiserver-arg+`, preserving other drop-ins. The user kubeconfig invokes `kubectl oidc-login get-token` and contains no credential or client secret. `aa login` fills `<cluster-ca-base64>` using an authenticated cluster CA distribution channel; it must never disable TLS verification.

## Configuration

Consumes `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, username/groups claim and prefix keys, `APISERVER_URL`, and `PROJECT_NAME`. The client must support a public PKCE browser flow and the groups scope. Kubernetes prefixes (`oidc:` in the fixture) are applied by the API server. Argo consumes raw groups/sub claims from the IdP; the Argo confidential client uses the same configured client ID only if the provider supports both flows safely (VERIFY). Register its callback separately.

k3s: stage as `/etc/rancher/k3s/config.yaml.d/20-oidc.yaml` on every server and restart under maintenance. kubeadm: pass the equivalent six flags to kube-apiserver via the kubeadm extraArgs/static Pod configuration. Managed EKS/GKE/AKS equivalents are in the [cluster table](../README.md); provider exec credentials may require coordinated subject mapping and an aa login adapter. VERIFY pinned k3s supports config drop-ins and append semantics.

## Secrets

No API-server OIDC client secret is required for ID-token validation. Bootstrap credentials remain encrypted offline for break-glass only. The plugin's refresh-token cache is a per-user secret and must be protected by workstation disk encryption and restrictive file permissions.

## Deploy

Render, register the provider client/scopes, install server configuration, test a non-admin OIDC login and group bindings, then escrow bootstrap credentials. No static admin kubeconfigs on laptops.

## Verify

Template tests parse both files. Live: verify issuer discovery/JWKS reachability from each server, exact audience/issuer, groups and subject via `kubectl auth whoami`, allowed holder actions and denied cross-user exec, logout/token revocation and clock skew. Validate provider group prefix behavior explicitly.

## Rollback

Restore the prior server identity configuration through the offline break-glass procedure, preserve audit logs, and reissue user credentials after fixing the IdP.

## Security notes

Never copy a bootstrap credential into the user template. OIDC authentication is separate from RBAC and exec-guard authorization. Break-glass group membership has no standing binding.
