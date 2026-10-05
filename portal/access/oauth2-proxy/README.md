# OIDC access proxy

One oauth2-proxy instance per hostname avoids ambiguity in upstream routing and keeps callbacks and cookies isolated.

## Interface

Route each HOST_PANEL, HOST_GRAFANA, HOST_ARGOCD and HOST_HEADLAMP to its matching oauth2-proxy Service.

## Configuration

RENDER-IF selects org.network.access.kind == oidc-proxy. OIDC_ISSUER_URL and OIDC_CLIENT_ID configure login. Images are pinned by digest.

## Secrets

oidc-client/client_secret; oauth2-proxy/cookie_secret (random 32-byte base64 value).

## Deploy

Register https://<hostname>/oauth2/callback for all four hosts. A TLS ingress outside this component must preserve Host, overwrite forwarded headers and target matching Services. Argo TLS must trust its certificate. Files grafana-ingress and argocd-ingress are integration examples; their task applies them in backend namespaces.

## Verify

Run access/tests. Verify unauthorized denial and org-group token claims after deployment.

## Rollback

Remove ingress routes before removing proxies.

## Security notes

The proxy replaces Authorization with an ID token; the panel independently verifies issuer, audience, expiry and subject. No email allowlist is used. Backends retain their own authorization. See [oauth2-proxy configuration](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview/).
