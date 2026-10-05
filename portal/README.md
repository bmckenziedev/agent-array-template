# Identity-aware portal

The portal provides the panel, access proxy variants and Headlamp. Application authentication uses the org OIDC directory; Kubernetes actions in Headlamp retain the user identity.

## Interface

Panel APIs and Services on public 8080 and internal 8081; Headlamp on 4466. Access variants expose only ClusterIP Services.

## Configuration

Global HOST_*, NS_*, ACCESS_KIND, OIDC_* and GROUP_* keys. Panel defaults are declared beside the panel application. Session dispatch is optional.

## Secrets

panel-env, litellm-mint, oidc-client, oauth2-proxy or cloudflared-token, by selected variant.

## Deploy

Render with tools/render/render.py; Argo consumes rendered/global/portal. Register callback URLs before publishing hostnames.

## Verify

python -m unittest discover -s portal/access/tests; Docker harness instructions are in e2e/README.md.

## Rollback

Remove hostname routes, restore prior manifests and panel image; preserve panel-data.

## Security notes

The public proxy cannot access the internal listener. Org namespaces and default deny policies come from platform/hardening.
