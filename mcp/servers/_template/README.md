# Build an MCP server for an internal API

This template supplies a minimal stdlib streamable HTTP MCP implementation and an explicit
pod-identity boundary. Replace `example_read` with an adapter to an organization's internal API;
all returned text remains untrusted data.

## Interface

POST `/mcp` accepts JSON-RPC `initialize`, `tools/list`, `tools/call` and `ping`.
Authenticated notifications return 202 with no body. Responses are ordinary JSON; this is
the stateless streamable HTTP subset, with no SSE stream or server-initiated notifications.
GET `/healthz` and `/metrics` expose health and `aa_mcp_*` counters and a latency histogram.
The single-process HTTPServer executes calls serially; replicas may be added without identity
or lease state in memory. It is a starting point, not a general MCP gateway.

`aa_mcp.py` supplies `Auth`, `Directory`, `Kubernetes`, `Application`, bounded results and schema
validation. Authentication sends the bearer token to Kubernetes TokenReview with audience
`<project>-mcp`, requires an authenticated response with that audience and exact SA name
`system:serviceaccount:<ns>:session`, verifies namespace prefix and `kind: user-sessions`, maps
the namespace user label through `users.json`, then intersects `mcp-entitlements.json` with
the directory team's server grants. Non-active users, missing references and errors fail closed.
Denied requests return HTTP 403 with JSON-RPC code -32003. Every dispatched request emits an
audit event containing actor, tool, team, outcome and a safe reason, never the payload.

## Configuration

Required environment names: `PROJECT_NAME`, `LABEL_PREFIX`, `USER_NS_PREFIX`, `APISERVER_URL`,
`MCP_NAME`. `MCP_PORT` defaults to 8080 and `MCP_PATH` to `/mcp`; `--port` overrides the port.
The directory mounts at `/etc/agent-array/org` (`users.json`, `teams.json`, `accounts.json`);
team entitlements are in `/etc/agent-array/org/mcp-entitlements.json`. Both are read
again on every request so a long-lived process does not cache revoked team membership.
Kubernetes client calls use the server's in-cluster SA token and CA, with certificate verification.

Request bodies are capped at 128 KiB. Untrusted output is clipped at 48,000 UTF-8 bytes before
the outer JSON wrapper, which includes `truncated`. JSON encoding can increase the wire size;
it remains bounded. Upstream responses are capped at 2 MiB and individual upstream requests
time out after 60 seconds. Tool schemas reject unexpected keys and validate types and bounds.

## Secrets

Add a registry `server_secret` reference for an upstream credential. The deployment mounts
the named key read-only as `/etc/agent-array/upstream/credential`; the adapter should read it
only when necessary and never include it in a response, exception or audit event. There is
no credential embedded in the example. Session pod bearer tokens are transient caller identity.

## Deploy

Copy the template to an organization server component, implement explicit tools with narrow
schemas and fixed upstream endpoints, add registry/team grants and build with repository root context:

```sh
docker build --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<approved-digest> -f mcp/servers/_template/Dockerfile .
```

The mandatory `PYTHON_IMAGE` build argument has no floating default: CI must supply a reviewed
immutable digest. Publish a digest-pinned image and configure the registry using
[example-values.yaml](example-values.yaml). Render the shared per-MCP deployment, Service,
SA/TokenReview RBAC and policies. The image runs as UID 10001 with a read-only root filesystem.
`server.py` is executable and exits 0 on normal completion; argparse errors exit 2.

## Verify

Run `python -m unittest discover -s mcp/servers/_template/tests`.
The fake TokenReview is in process. HTTP tests bind loopback port 0 and always close and join
the server. Add adapter tests for upstream ACLs, pagination, result limits and error sanitization.
Platform admins verify TokenReview audience and directory refresh in cluster before granting access.

## Rollback

Remove team grants and source references, render/sync and restart sessions, then roll back
the adapter image. Never work around failed authentication by enabling anonymous access.

## Security notes

All external text is wrapped as untrusted data. Use only fixed API endpoints and encoded IDs,
not caller-supplied URLs or executable commands. Fail closed on identity or directory errors.
The stock example returns synthetic data; upstream ACL enforcement belongs in the adapter.
The `aa_mcp.py` runtime is also packaged with arrayops for its independent image copy; a test
requires both copies to remain byte-identical.
