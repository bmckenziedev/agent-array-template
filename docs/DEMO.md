# Local demo on a disposable cluster

This walkthrough stands the template up on a throwaway single-node cluster on one laptop in
about 30 minutes. The path runs from rendering through the Argo CD app-of-apps to one synthetic
user namespace with a locally built session image, the pace service and the template MCP server.
Step times assume the base images and the chart are already cached; first downloads add time.
It is a learning aid. It is not an adoption path, and its evidence clears no VERIFY gate. Follow
[adoption](ADOPTION.md) for a real deployment.

Everything here is synthetic. The demo needs no IdP, vendor seat, API account, GPU or
organisation Git host, and it must never run against a cluster, registry or kubeconfig context
that already serves anything else. Markers written **VERIFY** were not proved from this
repository. Each is a pinned-tool or cluster behaviour you must check on your own laptop.

## Choice of cluster: k3d

Use **k3d** (k3s in Docker). kind would also work, but k3d needs fewer deviations:

| Need | k3d | kind |
|---|---|---|
| Match `cluster.distribution: k3s` and the org example's CIDRs (`10.42/16`, `10.43/16`, DNS `10.43.0.10`, API Service `10.43.0.1`) | k3s defaults, unchanged | Different defaults; `distribution: generic` and every CIDR edited |
| NetworkPolicy enforcement for default-deny | k3s embedded kube-router policy controller, matching `cni: kube-router` | Depends on the kindnet version; **VERIFY** before trusting |
| Default storage class `local-path` | Built in | Different provisioner and class name |
| Local image registry the cluster can pull digests from | `k3d registry create` plus `--registry-use` | Manual registry container and containerd patch |

k3d does not provide Kata, and neither does kind on a laptop. That gap is the largest deviation
from the template and is covered in the next section.

## What the demo skips, and why

| Component (Argo wave) | Demo treatment | Reason |
|---|---|---|
| Kata (02) | **Skipped.** A RuntimeClass named `demo-runc` uses the plain `runc` handler | Kata needs hardware virtualization inside the node container. The rename keeps every admission check (`runtimeClassName == RUNTIME_CLASS_VM`) intact while showing in every manifest that no VM boundary exists |
| Sealed-secrets and secrets (10) | **Skipped.** The two Secrets the demo needs are created with `kubectl` from throwaway values | No controller key custody exists for a disposable cluster. Never do this outside the demo |
| LiteLLM gateway (30) | **Skipped** | It needs organisation API account Secrets, Postgres and Redis. The demo makes no model calls |
| Farm MCP (56), portal (60) | **Skipped** | Both need a LiteLLM mint key and an OIDC access proxy |
| Monitoring (80), backup | **Skipped**, `backup.kind: none` | Optional for the walkthrough; heavy on a laptop |
| All optional modules (70), Kimi | **Disabled** (the default) | GPU lanes need a GPU, and every module starts disabled |
| OIDC on the API server, Argo SSO | **Skipped.** Argo runs with `argocd --core` on your admin kubeconfig. Holder access is shown with `kubectl --as` impersonation | No IdP. Local Argo admin stays disabled as rendered |
| Vendor login | **Skipped.** The seat row stays in the registry, but nobody logs in | No vendor seats are used. The session pod starts and waits for a login that never happens |
| Encrypted login root | **Skipped.** The login root is a plain host directory | It holds no credentials because no login occurs |
| Strict render | **Skipped**; non-strict render warns | Images the demo does not build keep all-zero digests, and `--strict` refuses those |

Kept as rendered: hardening (00), org directory (05), cluster/RBAC/login storage (08), pace (35),
sessions platform with its admission webhook (40), MCP TokenReview (55) and the users, teams and
MCP ApplicationSets.

## Prerequisites

- x86_64 Linux with Docker, 4 CPUs and 8 GiB of free memory. The session image refuses other
  architectures (`test "$(uname -m)" = x86_64` in its Dockerfile), so Apple Silicon is not
  supported. A Linux host filesystem (ext4, xfs or btrfs) is needed for the login bind mount; see
  step 8.
- `k3d` v5, `kubectl`, `helm` v3, `argocd` CLI, `git`, `openssl`, Python 3.10+ and `make`.
- Network access from the laptop for image and chart pulls only. The cluster never contacts a
  Git forge.
- A fresh clone of this repository, on a new branch:

```sh
git switch -c demo
mkdir -p "$HOME/aa-demo/logins" "$HOME/aa-demo/git"
```

## Step 1: Create the registry and cluster (2 min)

The pod-security exemption file below is a demo-only deviation. Without it, the k3s local-path
helper pod, which mounts a hostPath, runs in `kube-system`. Hardening labels that namespace
`baseline`, which forbids hostPath, so pace's PVC would never bind. The template does not address
this for k3s's built-in storage (**VERIFY** on a real k3s adoption).

```sh
cat > "$HOME/aa-demo/psa.yaml" <<'EOF'
apiVersion: apiserver.config.k8s.io/v1
kind: AdmissionConfiguration
plugins:
- name: PodSecurity
  configuration:
    apiVersion: pod-security.admission.config.k8s.io/v1
    kind: PodSecurityConfiguration
    defaults: {enforce: privileged, audit: privileged, warn: privileged}
    exemptions: {namespaces: [kube-system]}
EOF
k3d registry create aa-registry --port 5050
k3d cluster create aa-demo \
  --image rancher/k3s:v1.36.5-k3s1 \
  --servers 1 --agents 0 \
  --registry-use k3d-aa-registry:5050 \
  --volume "$HOME/aa-demo/logins:/var/lib/agent-array/logins@server:0" \
  --volume "$HOME/aa-demo/git:/srv/git@server:0" \
  --volume "$HOME/aa-demo/psa.yaml:/etc/aa-demo/psa.yaml@server:0" \
  --k3s-arg "--pod-security-admission-config-file=/etc/aa-demo/psa.yaml@server:0" \
  --k3s-arg "--disable=traefik@server:0" \
  --k3s-arg "--disable=metrics-server@server:0" \
  --wait
kubectl config current-context
kubectl get nodes -o wide
NODE_IP=$(kubectl get endpoints kubernetes -o jsonpath='{.subsets[0].addresses[0].ip}')
echo "$NODE_IP"
```

Expected output: the context is `k3d-aa-demo`, and one node, `k3d-aa-demo-server-0`, is `Ready`
with version `v1.36.5+k3s1`. `NODE_IP` is the server container's address on the k3d Docker
network, and the API serves it on port 6443. The image tag must match `cluster.version` in
org.yaml (**VERIFY** that this k3s tag is published). metrics-server is disabled because
hardening's default-deny in `kube-system` would make its APIService unavailable and fill every
`kubectl` call with discovery warnings.

## Step 2: Prepare demo registries (4 min)

```sh
cp org/org.example.yaml org/org.yaml
for f in teams users accounts estates; do cp "org/$f.example.yaml" "org/$f.yaml"; done
cp mcp/registry.example.yaml mcp/registry.yaml
cp mcp/context-sources.example.yaml mcp/context-sources.yaml
```

Edit `org/org.yaml`. Replace the blocks below and leave everything else as copied. Put the
`NODE_IP` value from step 1 in place of `NODE_IP`. The two image digests and the CA bundle are
filled in during steps 3 and 4.

```yaml
github:
  org: example-org
  repo: agent-array
  git_remote: "git://aa-git.aa-demo-git.svc.cluster.local/agent-array.git"
  revision: demo
  platform_team_handle: "@example-org/platform"

registry:
  host: "k3d-aa-registry:5050"
  namespace: demo
  pull_secret: registry-pull

# in images: change only these two rows
  session-claude: {tag: "0.1.0", digest: "sha256:SESSION_CLAUDE_DIGEST"}
  pace: {tag: "0.1.0", digest: "sha256:PACE_DIGEST"}

# in cluster: change these keys
  provider: other
  apiserver:
    endpoint_ips: [NODE_IP]
    port: 6443
    service_ip: 10.43.0.1
    url: https://NODE_IP:6443
  runtime_classes:
    vm: demo-runc
    gvisor: gvisor
    gpu: nvidia

nodes:
  - name: k3d-aa-demo-server-0
    short: demo
    roles: [control-plane, worker, sessions]
    overlay_ip: NODE_IP
    runtime_classes: [demo-runc]

# in network: change only overlay
  overlay: none

# in backup: change only kind
  kind: none

files:
  teams: org/teams.yaml
  users: org/users.yaml
  accounts: org/accounts.yaml
  estates: org/estates.yaml
  mcp_registry: mcp/registry.yaml
  context_sources: mcp/context-sources.yaml

components:
  sessions: {admission_ca_bundle: "CA_BUNDLE_BASE64"}
```

Replace `org/users.yaml` with one active synthetic user. Bo stays as an offboarded row only
because Bo is the `platform` team lead, and the renderer requires every lead to exist.

```yaml
version: 1
users:
  - slug: ana
    oidc_sub: "00u1example0ana0000"
    email: ana@example.org
    github: ana-example
    teams: [payments]
    primary_team: payments
    tier: null
    status: active
    tools:
      claude: {account: acct-claude-seat-ana, home_node: k3d-aa-demo-server-0, replicas: 1}
  - slug: bo
    oidc_sub: "00u1example0bo00000"
    email: bo@example.org
    github: bo-example
    teams: [platform, payments]
    primary_team: platform
    tier: null
    status: offboarded
    tools: {}
tombstones: [old-user]
```

In `mcp/registry.yaml`, delete the `farm` and `tickets` entries. Farm is skipped, and tickets
points at a placeholder image. Then append the template server:

```yaml
  - name: demo-api
    description: synthetic records from the MCP server template (demo only)
    transport: http
    deploy: {image: "k3d-aa-registry:5050/demo/mcp-demo-api@sha256:MCP_DIGEST", port: 8080, path: /mcp}
    auth: pod-identity
    clients: [claude, codex]
    allowed_teams: ["*"]
    context_tags: [demo]
    tool_timeout_s: 60
```

The template's `example-values.yaml` declares `server_egress` and `server_secret`. The demo
omits both: the stock `example_read` returns synthetic text and calls no upstream.

In `mcp/context-sources.yaml`, delete the `tickets` source. In `org/teams.yaml`, set:

- payments: `mcp_servers: [factory, demo-api]` and `context_sources: [estate-snapshot, estate-index]`
- platform: `mcp_servers: [factory, arrayops]`

Validate the configuration before building anything:

```sh
python tools/render/render.py --validate-only; echo "exit=$?"
```

Expected output: until step 3 fills in the digests, the only error is
`error: images.session-claude: bad digest` (exit 2). Any other `error:` line names a registry
edit mistake, for example `error: team payments: unknown mcp server tickets`. After step 3 the
command prints only the `warning:` lines listed in step 5 and exits 0.

## Step 3: Build and push images (8 min; mostly the session image)

The repository root is the build context for every image. Pushes go to `localhost:5050`. The
cluster pulls the same repository path as `k3d-aa-registry:5050`, so the digest is identical.

```sh
docker pull python:3.12-slim                # note the printed "Digest: sha256:..."
PY_DIGEST=sha256:...                        # paste it
docker build -f sessions/claude/image/Dockerfile -t localhost:5050/demo/agent-array-session-claude:0.1.0 .
docker build -f services/pace/Dockerfile -t localhost:5050/demo/agent-array-pace:0.1.0 .
docker build --build-arg "PYTHON_IMAGE=python:3.12-slim@$PY_DIGEST" \
  -f mcp/servers/_template/Dockerfile -t localhost:5050/demo/mcp-demo-api:0.1.0 .
for i in agent-array-session-claude agent-array-pace mcp-demo-api; do
  docker push "localhost:5050/demo/$i:0.1.0" | tail -n 1
done
```

Expected output: the session build prints `claude --version` output for the pinned CLI
(2.1.285) and ends successfully. Each push ends with
`0.1.0: digest: sha256:<64 hex> size: <n>`. Copy the three digests into `SESSION_CLAUDE_DIGEST`,
`PACE_DIGEST` and `MCP_DIGEST`.

The session image also serves the admission webhook, which runs `aa-admission-webhook` from the
same image. The other image rows stay at all-zero digests because their components are skipped.

## Step 4: Admission webhook TLS (1 min)

The sessions lookup webhook has `failurePolicy: Fail`, so session PVCs, pods and login PVs cannot
be admitted until it serves TLS that the API server trusts. Create a throwaway CA and serving
certificate:

```sh
d="$HOME/aa-demo/tls"; mkdir -p "$d"; cd "$d"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=aa-demo-ca" -keyout ca.key -out ca.crt
openssl req -newkey rsa:2048 -nodes -subj "/CN=session-admission.agent-array-system.svc" \
  -keyout tls.key -out tls.csr
printf 'subjectAltName=DNS:session-admission.agent-array-system.svc\n' > san.ext
openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 -extfile san.ext -out tls.crt
base64 -w0 ca.crt; echo
cd - >/dev/null
```

Paste the base64 output into `CA_BUNDLE_BASE64` in org.yaml. It is a public certificate, never
the key. The key stays under `$HOME/aa-demo`, outside the repository.

## Step 5: Render (1 min)

```sh
make render
ls rendered/users rendered/mcp rendered/global/argocd/k8s
```

Expected output: the renderer prints nothing on stdout. On stderr it prints one warning per
image that is still all-zero:

```text
warning: images.session-codex: all-zero digest (refused by --strict)
warning: images.session-kimi: all-zero digest (refused by --strict)
warning: images.panel: all-zero digest (refused by --strict)
warning: images.session-runner: all-zero digest (refused by --strict)
warning: images.farm-mcp: all-zero digest (refused by --strict)
warning: images.factory: all-zero digest (refused by --strict)
warning: images.hermes-chat: all-zero digest (refused by --strict)
warning: C_SUPERVISOR_IMAGE: all-zero digest (refused by --strict)
```

The listings show `ana` under `rendered/users`, `demo-api` under `rendered/mcp`, and
`apps`, `argocd-rbac-cm.yaml`, `projects.yaml` and `root.yaml` under the Argo directory
(**VERIFY** the exact file names). Inspect `rendered/users/ana` (namespace, holder RBAC, quota,
StatefulSet, MCP ConfigMap) and `rendered/files/SECRETS-REQUIRED.md`.

## Step 6: Publish the demo branch to an in-cluster Git daemon (2 min)

Argo CD reads Git, not your working tree. `/rendered/` is gitignored in the template, so it is
force-added on this branch only.

```sh
git add org/*.yaml mcp/registry.yaml mcp/context-sources.yaml
git add -f rendered
git commit -m "demo: synthetic single-node render"
git clone --bare . "$HOME/aa-demo/git/agent-array.git"
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Namespace
metadata: {name: aa-demo-git}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: aa-git, namespace: aa-demo-git}
spec:
  replicas: 1
  selector: {matchLabels: {app: aa-git}}
  template:
    metadata: {labels: {app: aa-git}}
    spec:
      containers:
      - name: git
        image: alpine/git:latest
        command: [git, daemon, --reuseaddr, --export-all, --verbose, --base-path=/srv/git, /srv/git]
        env:
        - {name: GIT_CONFIG_COUNT, value: "1"}
        - {name: GIT_CONFIG_KEY_0, value: safe.directory}
        - {name: GIT_CONFIG_VALUE_0, value: "*"}
        ports: [{containerPort: 9418}]
        volumeMounts: [{name: repos, mountPath: /srv/git, readOnly: true}]
      volumes: [{name: repos, hostPath: {path: /srv/git, type: Directory}}]
---
apiVersion: v1
kind: Service
metadata: {name: aa-git, namespace: aa-demo-git}
spec:
  selector: {app: aa-git}
  ports: [{port: 9418, targetPort: 9418}]
EOF
kubectl -n aa-demo-git rollout status deploy/aa-git
```

Expected output: `deployment "aa-git" successfully rolled out`. This namespace is demo
scaffolding outside the org model, so hardening does not manage it. Pin the `alpine/git` image
by digest if you keep the demo for longer than a session. After any later change, re-render,
commit and run `git push "$HOME/aa-demo/git/agent-array.git" demo`.

**VERIFY:** Argo CD accepts `git://` repository URLs. If it refuses them, use a throwaway private
repository on a forge you control with a read-only deploy key, and set `git_remote` to it. That
publishes the synthetic demo configuration to that forge.

## Step 7: Bootstrap hardening, runtime and node contract (2 min)

Hardening owns every namespace, including `argocd`, so it is applied once by hand before Argo
exists. Argo adopts it in step 10.

```sh
kubectl apply -R -f rendered/global/platform/hardening/k8s
kubectl apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: {name: demo-runc}
handler: runc
EOF
python cluster/node-contract/apply-node-contract.py --rendered rendered
python cluster/node-contract/apply-node-contract.py --rendered rendered --yes
kubectl get node k3d-aa-demo-server-0 --show-labels | tr ',' '\n' | grep agent-array
```

Expected output: namespaces such as `agent-array-system`, `agent-array-mcp` and `argocd` are
`created`, along with `default-deny`, `allow-cluster-dns` and `allow-apiserver-endpoint`
NetworkPolicies and the admission policies. The node-contract dry-run prints `kubectl label`
commands, and `--yes` applies them. The node then carries
`agent-array.example.org/role-sessions=true`, `role-control-plane`, `role-worker` and
`agent-array.example.org/runtime-demo-runc=true`. The k3s `runc` handler is configured by default
(**VERIFY**).

The repo-server's rendered egress allows public Git on ports 22 and 443 only. Add the explicit
private-Git policy that the [hardening guide](../platform/hardening/README.md) requires:

```sh
kubectl apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: demo-allow-repo-server-local-git, namespace: argocd}
spec:
  podSelector: {matchLabels: {app.kubernetes.io/name: argocd-repo-server}}
  policyTypes: [Egress]
  egress:
  - to: [{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: aa-demo-git}}}]
    ports: [{protocol: TCP, port: 9418}]
EOF
```

## Step 8: Login home directory and demo Secrets (1 min)

The login PV is pre-bound to a host directory. Review the generated plan, then create the same
directories inside the node container. That path is the host bind mount from step 1, so the
CLI sees ext4, xfs or btrfs, which `entrypoint-check` accepts for `login_storage: disk`.

```sh
bash rendered/files/cluster/node-prep/k3d-aa-demo-server-0/prepare-login-homes.sh
docker exec k3d-aa-demo-server-0 sh -c '
  r=/var/lib/agent-array/logins; l=$r/ana/claude/k3d-aa-demo-server-0
  mkdir -p $l/projects $l/sessions
  chmod 0711 $r $r/ana $r/ana/claude
  chown -R 1000:1000 $l && chmod 0700 $l $l/projects $l/sessions'
kubectl -n agent-array-system create secret tls session-admission-tls \
  --cert="$HOME/aa-demo/tls/tls.crt" --key="$HOME/aa-demo/tls/tls.key"
kubectl -n argocd create secret generic argocd-oidc --from-literal=clientSecret=demo-unused
kubectl -n argocd label secret argocd-oidc app.kubernetes.io/part-of=argocd
```

Expected output: the dry-run prints `install -d -o ... -m 0711|0700 ...` lines for
`/var/lib/agent-array/logins`, `.../ana`, `.../ana/claude`, the login directory and its `projects`
and `sessions` subdirectories. The `docker exec` call prints nothing. Each Secret command prints
`secret/... created`. The generated script itself needs bash, root and the node hostname. That is
why the demo runs equivalent commands through `docker exec`.

## Step 9: Install Argo CD (3 min)

```sh
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update
ARGOCD_NAMESPACE=argocd bash argocd/install.sh          # prints the complete Helm command
ARGOCD_NAMESPACE=argocd bash argocd/install.sh --yes
kubectl -n argocd get pods
kubectl config set-context --current --namespace=argocd
argocd login --core
```

Expected output: the first call prints
`helm upgrade --install argo-cd argo/argo-cd --version 10.9.6 --namespace argocd -f rendered/files/argocd/helm/argocd/values.yaml --wait --timeout 8m`.
The second call installs the chart and returns after the pods are Ready. Rendered values disable
local admin, and the OIDC issuer `idp.example.org` is unreachable, so the UI cannot log in. Core
mode uses your kubeconfig instead. **VERIFY** that the chart's pods satisfy restricted PSA in
`argocd` and that the server starts with an unreachable issuer.

## Step 10: App-of-apps (3 min)

Projects and the RBAC ConfigMap are bootstrapped by hand, as in [GitOps](../argocd/README.md).
Then sync only the root's children that the demo keeps.

```sh
kubectl apply -f rendered/global/argocd/k8s/projects.yaml \
  -f rendered/global/argocd/k8s/argocd-rbac-cm.yaml \
  -f rendered/global/argocd/k8s/root.yaml
argocd app sync agent-array-root \
  --resource argoproj.io:Application:agent-array-00-platform-hardening \
  --resource argoproj.io:Application:agent-array-05-org-directory \
  --resource argoproj.io:Application:agent-array-08-cluster \
  --resource argoproj.io:Application:agent-array-35-pace \
  --resource argoproj.io:Application:agent-array-40-sessions-platform \
  --resource argoproj.io:Application:agent-array-55-mcp
argocd app sync agent-array-00-platform-hardening
argocd app sync agent-array-08-cluster
argocd app sync agent-array-40-sessions-platform
kubectl -n agent-array-system rollout status deploy/session-admission
kubectl -n agent-array-system rollout status deploy/pace
argocd app sync agent-array-root \
  --resource argoproj.io:ApplicationSet:agent-array-users \
  --resource argoproj.io:ApplicationSet:agent-array-teams \
  --resource argoproj.io:ApplicationSet:agent-array-mcp
argocd app list
```

Applications 05, 35 and 55 sync automatically once the root creates them. 00, 08 and 40 are
manual by design. The ApplicationSets go last so that the webhook is serving before Ana's
PVC and pods are submitted.

Expected output: `argocd app list` shows `agent-array-root` as `OutOfSync`, because the skipped
children were deliberately not created. The selected platform apps show `Synced`/`Healthy`, and
`user-ana`, `mcp-demo-api` and the `team-*` apps appear and turn `Synced`. Skipped apps
(02, 10, 30, 56, 60, 80) are absent. **VERIFY** the `--resource GROUP:KIND:NAME` syntax on your
`argocd` CLI version.

## Step 11: Check the result (3 min)

Pace:

```sh
kubectl get --raw /api/v1/namespaces/agent-array-system/services/pace:8080/proxy/healthz; echo
kubectl get --raw /api/v1/namespaces/agent-array-system/services/pace:8080/proxy/v1/accounts | head -c 400; echo
```

Expected output: `{"status":"ok"}`, then account metadata with no secret references.

User namespace and session pod:

```sh
kubectl get ns aa-u-ana --show-labels
kubectl -n aa-u-ana get statefulset,pod,pvc,configmap
kubectl -n aa-u-ana logs claude-demo-0 -c claude | head
kubectl -n aa-u-ana exec claude-demo-0 -c claude -- true
kubectl auth can-i create pods/exec -n aa-u-ana --as oidc:00u1example0ana0000
kubectl auth can-i get pods -n aa-u-ana --as oidc:00u1example0bo00000
```

Expected output:

- The namespace carries `agent-array.example.org/kind=user-sessions` and the user/team labels.
- StatefulSet `claude-demo` is `1/1` and pod `claude-demo-0` is `Running` with `2/2` containers
  (`claude` and `estate`). PVC `claude-home-demo` is `Bound` to
  `aa-u-ana-claude-home-demo`, and ConfigMaps `claude-policy`, `claude-mcp` and
  `context-policy` are present.
- The log shows `aa: preflight OK`. Inside tmux, `aa-rc` repeats
  `Seat holder login required: claude auth login in the shell window.` No vendor login
  happens in this demo.
- `exec` as the k3d admin is denied with
  `Only the holder or ticketed break-glass may connect to a session.`
  This is the R5 guard working. **VERIFY** CONNECT admission on k3s 1.36 and the exact
  error format.
- `can-i` answers `yes` for Ana's OIDC subject and `no` for Bo's (**VERIFY** the subject
  format `oidc:<sub>` against the rendered RoleBinding).
- An event `FailedToRetrieveImagePullSecret` for `registry-pull` is expected. The k3d
  registry needs no credentials.

MCP server and its identity boundary. Run the port-forward in a second terminal and stop it with
Ctrl-C when you finish:

```sh
kubectl -n agent-array-mcp get deploy,svc mcp-demo-api
kubectl -n aa-u-ana get cm claude-mcp -o jsonpath='{.data.managed-mcp\.json}'; echo
# second terminal:
kubectl -n agent-array-mcp port-forward svc/mcp-demo-api 18080:8080
# first terminal:
TOKEN=$(kubectl -n aa-u-ana create token session --audience agent-array-mcp --duration 10m)
REQ='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl -s -H 'Content-Type: application/json' -d "$REQ" http://127.0.0.1:18080/mcp; echo
curl -s -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d "$REQ" http://127.0.0.1:18080/mcp; echo
WRONG=$(kubectl -n aa-u-ana create token session --audience agent-array-pace --duration 10m)
curl -s -H 'Content-Type: application/json' -H "Authorization: Bearer $WRONG" -d "$REQ" http://127.0.0.1:18080/mcp; echo
kubectl -n agent-array-mcp logs deploy/mcp-demo-api | tail -n 3
```

Expected output:

- `managed-mcp.json` lists `demo-api` as `type: http`, with a cluster-local URL and the
  `aa-mcp-token` headers helper, plus the `factory` stdio entry.
- Without a token: HTTP 403 with a JSON-RPC error of code `-32003` and message `forbidden`.
- With Ana's MCP-audience token: a `result` whose tools list contains `example_read`.
- With the pace-audience token: 403 `forbidden` again.
- Audit lines are JSON with `outcome` `deny` (`missing-bearer`, `tokenreview`) and `allow`, and
  never include the token.

**VERIFY:** the port-forward reaches the pod despite the ingress policy, which only admits
user-session pods. Port-forward traffic enters the pod's network namespace directly.

## Common failures

| Symptom | Cause | Fix |
|---|---|---|
| Session image build fails at `test "$(uname -m)" = x86_64` | Non-x86_64 host | Use an x86_64 Linux machine or VM |
| `error: images.session-claude: bad digest` | Placeholder left in org.yaml | Paste the full `sha256:` digest from step 3 |
| `error: team payments: unknown mcp server tickets` (or `farm`) | Registry trimmed but teams not edited | Apply the step 2 teams edits |
| `error: user ana: home_node ... is not a sessions node` | Node name mismatch | Use `k3d-aa-demo-server-0` and role `sessions` |
| Argo apps `Unknown`, repo errors `connection refused`/`timeout` | Repo-server egress blocked or Git daemon down | Check the step 7 extra NetworkPolicy and `kubectl -n aa-demo-git logs deploy/aa-git` |
| Argo apps `ComparisonError: ... not found` after edits | The bare repo was not updated | `git push "$HOME/aa-demo/git/agent-array.git" demo` |
| `failed calling webhook "sessions.example.org"` | Webhook not Ready or CA mismatch | Check `session-admission` pods and that `admission_ca_bundle` is the base64 of `ca.crt`, then re-render, commit, push and re-sync 40 and `user-ana` |
| Pace pod `Pending`, PVC `Pending` and `helper-pod` forbidden events | kube-system PSA exemption missing | Recreate the cluster with the step 1 `--pod-security-admission-config-file` flag |
| Session pod `Pending` with `didn't match Pod's node affinity/selector` | Node contract labels missing | Re-run `apply-node-contract.py --rendered rendered --yes` |
| Session pod `CreateContainerConfigError`/`no runtime handler` | `demo-runc` RuntimeClass missing | Apply the step 7 RuntimeClass |
| Log `aa: preflight refused: login directory must be owned by the pod uid with mode 0700` | Step 8 directory ownership | Re-run the `docker exec` block |
| Log `aa: preflight refused: login storage must be disk; observed overlay` | Login path not on the host bind mount, or a non-Linux host filesystem | Recreate the cluster with the step 1 `--volume ...logins...` flag |
| `ErrImagePull ... k3d-aa-registry:5050` | Cluster created without `--registry-use`, or image not pushed | Recreate with the flag; `docker push` again |
| Everything denied after a Docker restart | `NODE_IP` changed; API endpoint policies are pinned to it | Update org.yaml, re-render, commit, push, re-apply hardening and re-sync |

## Teardown (1 min)

```sh
docker exec k3d-aa-demo-server-0 rm -rf /var/lib/agent-array/logins/ana
k3d cluster delete aa-demo
k3d registry delete k3d-aa-registry
rm -rf "$HOME/aa-demo"
kubectl config get-contexts                    # k3d-aa-demo is gone
git switch main && git branch -D demo
rm -f org/org.yaml org/teams.yaml org/users.yaml org/accounts.yaml org/estates.yaml \
  mcp/registry.yaml mcp/context-sources.yaml && rm -rf rendered
```

Expected output: `k3d cluster delete` reports the cluster deleted, and `git status` is clean. The
login directory is removed from inside the node first because the container created it as root.
Nothing in this demo touched a seat, vendor console, IdP or shared registry, so nothing outside
the laptop needs revoking.
