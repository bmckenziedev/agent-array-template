# Headlamp

Headlamp provides a cluster interface with OIDC login and per-user Kubernetes authorization.

## Interface

Headlamp ClusterIP Service and a credential-free cluster kubeconfig. No RoleBinding, ClusterRoleBinding or shared service account credential.

## Configuration

OIDC_ISSUER_URL and OIDC_CLIENT_ID must match API-server authentication; HOST_HEADLAMP supplies the registered external hostname.

## Secrets

oidc-client/client_secret.

## Deploy

Register https://<HOST_HEADLAMP>/oidc-callback. The API server must trust the issuer/audience and map sub/groups with org prefixes. Role bindings are supplied by identity and session components.

## Verify

Run access/tests. Verify two users see only their authorized namespaces and audit logs show different OIDC subjects.

## Rollback

Remove the hostname route and restore the prior image.

## Security notes

The CA-only mount contains no credential; tokens are never auto-mounted. Kubernetes authorizes the browser OIDC token. See [Headlamp OIDC](https://headlamp.dev/docs/latest/installation/in-cluster/oidc/).
