# Agent-array user CLI

`aa` is the per-user OIDC client for sessions, approved estate transfers, pace
and the optional portal. It requires Python 3.10+, git, kubectl and the
int128 kubelogin `kubectl oidc-login` plugin. Windows and POSIX wrappers use
`AA_PYTHON` when set. Put this directory on PATH.

## Interface

```sh
aa init --from rendered/files/tools/aa/client.json
aa login
aa whoami
aa sessions list
aa sessions login --tool claude
aa sessions attach --tool claude --pod <discovered-pod>
aa sessions logs --tool claude
aa sessions scale 1 --tool claude
aa work up --tool claude --estate payments-core --path ~/payments-api --ws payments
aa work push --tool claude --estate payments-core --path ~/payments-api --ws payments
aa work get --tool claude --ws payments --out ~/exports/payments
aa snapshot up --tool claude --estate payments-core --path ~/payments-api --ws review
aa pace status
aa pace report --tool claude 5h=40,weekly=22
aa send --tool claude --estate payments-core --path ~/payments-api -m "Review changes"
aa get <task-id> --out ~/exports/task
```

Session names come from `<label prefix>/user` and `/tool` labels; select
`--pod` when several sessions are running. Namespace ownership is checked
against the namespace's `/user` and `/kind` labels. `-n` may name only the
caller namespace. No command accepts an alternate kubeconfig or context.
Scale clamps to the rendered tier maximum, refuses a request above the seat
maximum and checks aggregate replicas across the tool's StatefulSets.
Kubernetes quota and the pace lease check enforce concurrent admission again.

`whoami` reads `kubectl auth whoami` followed by `org-directory` in NS_SYSTEM.
The companion `aa-client-directory` holds computed OIDC subject, namespace
and tier maximum values; both directories must agree on user subject and slug.
The render plugin supplies this companion without adding policy to local config.

`work up`, `work push` and `snapshot up` send a complete sanitized **git archive
of committed HEAD**, as a tar stream, via holder-initiated `kubectl exec -i`.
Uncommitted and untracked files are excluded. `push` updates the receiver's
upstream state; the receiver must preserve the session branch and edits.
The remote helper protocol is:

```text
aa-snapshot work up|push --estate ID --ws NAME --tool TOOL --repo ORIGIN
aa-snapshot snapshot up --estate ID --ws NAME --tool TOOL --repo ORIGIN
aa-snapshot work get --ws NAME                             # stdout export tar
```

`work get` and portal downloads write only to a new or empty export directory.
Exports reject links, traversal, Windows device/stream names and case collisions;
they never apply changes to a checkout. Session work is ephemeral until exported.

`pace status` uses the API-server service proxy, then shows only caller seats
and eligible team API/pool accounts, including the service's window percentages
and caps. Manual reports execute `aa-usage-report --manual` in the caller pod.
Portal send/get use a fresh OIDC device flow and an in-memory bearer access token.
Uploads use multipart fields `brief`, `estate_id`, `repos` and `archive`; downloads use
`GET /api/tasks/<id>/bundle`. The portal remains authoritative for task ownership
and estate classification; `task-cache.json` is a disposable local response cache.

## Configuration

Config is `%APPDATA%/agent-array/config.json` on Windows and
`~/.config/agent-array/config.json` on POSIX; `AA_CONFIG_DIR` wins.
It contains exactly `cluster_api_url`, `ca_bundle_path`, `oidc_issuer`,
`oidc_client_id`, `label_prefix`, `user_namespace_prefix`, `NS_SYSTEM`,
`panel_url` and `pace_service_path`. URLs require HTTPS; an empty panel URL
disables send/get. Platform admins distribute the rendered client and CA bundle.

The template consumes APISERVER_URL, OIDC_ISSUER_URL, OIDC_CLIENT_ID,
LABEL_PREFIX, USER_NS_PREFIX and NS_SYSTEM. Component defaults:
`components.aa.ca_bundle_path` (`~/.config/agent-array/cluster-ca.pem`,
C_AA_CA_BUNDLE_PATH) and `components.aa.panel_url` (empty, C_AA_PANEL_URL).
RBAC templates use PROJECT_NAME, USER_SLUG, USER_NS, USER_OIDC_SUBJECT,
TEAM_ID and TEAM_OIDC_GROUP. Team members receive read access to the companion
directory; each user receives Namespace `get` on exactly one named namespace.

## Secrets

No static credentials are written. `login` writes `kubeconfig.json` containing
only server/CA references and the exec plugin; the plugin manages its own
short-lived token cache. Vendor login stays inside the holder pod. Config and
cache files receive mode 0600 on POSIX; Windows uses the user profile ACL.

## Deploy

Render org configuration and sync the tools/aa RBAC and companion ConfigMap.
Install kubelogin through an approved package manager or
`kubectl krew install oidc-login`. Run init and login using the distributed client.
Install the CA at its configured path. Login validates authentication before success.

## Verify

```sh
python -m unittest discover -s tools/aa/tests -t tools/aa -v
```

Offline tests use a fake kubectl on PATH, synthetic repositories and the canonical
org fixture. Template tests require PyYAML. The rewritten live check plans live
under `tests/live/`, require `--kubeconfig`, default to printing a dry-run plan,
and are never collected by the offline suite. Confirm official vendor login
commands and the receiver protocol in a disposable approved estate.
Exit 0 means success; exit 2 means configuration, policy, transport or usage failure.

## Rollback

Revert the distributed client and rendered companion directory together. Existing
exports remain local files. Remove the user config directory to discard context
and cache; revoke OIDC sessions at the identity provider separately.

## Security notes

Missing context-policy refuses every transfer. Estate origins and deny globs come
from `context-policy` (`estates.json`); the vendor must be allowed for the estate
data class by its entitled team policy. Agent configuration is stripped at every
depth: `.mcp.json`, `.claude/`, `.codex/`, `.kimi-code/`, `.agents/`. Credential
filenames are withheld and detected secret content refuses the transfer.
No override bypasses the secret scan or estate gate.

An estate owner is taken from the context entry or the directory's `estates.json`;
if neither supplies it, a single team membership is required. Ambiguous ownership
refuses a transfer rather than borrowing a more permissive team's vendor policy.

The pod-side **aa-snapshot must repeat estate allowlisting, data-class/vendor
authorization, deny filtering, path validation and agent-config stripping** at
ingest. Client checks improve feedback; server policy and holder RBAC enforce the
boundary. Neither a local config edit nor a forged upload may authorize an estate.
Seats are interactive; automation routes through pace API/pool accounts.

`aa sessions supervise --tool <tool> [--pod <pod>] -- <aa-supervise arguments>`
selects the holder pod and runs the private sidecar CLI through `kubectl exec -i`.
The default supervisor command is `list`; spawn reads its brief from stdin.
