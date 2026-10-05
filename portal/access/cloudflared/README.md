# Cloudflare Access proxy

The outbound tunnel supports organizations that choose Cloudflare Access instead of a generic OIDC proxy.

## Interface

Remotely managed routes target panel, Grafana, Argo CD and Headlamp ClusterIP Services. Never route internal 8081 or the API server.

## Configuration

RENDER-IF selects org.network.access.kind == cloudflare-access. CF_ACCESS_TEAM is the issuer team; panel-env supplies CF_ACCESS_AUD.

## Secrets

cloudflared-token/token, named by secrets.required.yaml.

## Deploy

Create separate Access applications before routes, enforce MFA and entitlement policies, and enable origin JWT protection. Configure organization sub/group mapping; panel directory oidc_sub must equal the verified Access sub. No email-based fallback.

## Verify

Run access/tests. Verify wrong audience and missing-token denial with synthetic accounts before opening routes.

## Rollback

Disable tunnel routes or scale cloudflared to zero.

## Security notes

The panel independently checks Access JWTs; Headlamp still uses org OIDC login and forwards each user token to Kubernetes. No service-token bypass should be granted for user routes.
