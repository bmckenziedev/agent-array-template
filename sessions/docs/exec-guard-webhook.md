# Session admission lookup and exec guard

The namespace-scoped policies select `kind: user-sessions`, never a list of namespace names. Run the
positive server dry-runs before enabling Deny: an ordinary busybox Pod and Deployment outside a
session namespace must pass, then a valid rendered session Pod must pass. Do not run the live checks
during the export build.

## Exec, attach and port-forward

The `aa-session-exec` ValidatingAdmissionPolicy handles CONNECT directly; no CONNECT
webhook is required. It matches `pods/exec`, `pods/attach` and `pods/portforward` only
in namespaces labelled `<label-prefix>/kind: user-sessions`. Namespace annotations
`<label-prefix>/oidc-sub` and `<label-prefix>/breakglass-ticket` establish the holder
and ticket. Exact prefixed holder identity or prefixed break-glass membership with a
nonempty ticket is required. The binding uses Deny and Audit. Optional annotations
and groups are guarded with `has()`.

Positive and negative server dry-runs remain deployment prerequisites. Alert on
Audit-action events and every connection by the break-glass group. The configured
example prefix is `oidc:`; deployment registry values supply the label prefix.

The remaining lookup webhook protects Pod/PVC/PV storage relationships. Its Service
is `session-admission` in the system namespace, port `https` (443 to 8443), with TLS
from Secret `session-admission-tls` and the configured public CA bundle. Its
`failurePolicy` is Fail; it receives Pod, StatefulSet, PVC and login PV CREATE/UPDATE,
never CONNECT. Provision TLS and bootstrap it before enabling the bindings.

## PVC and PV ownership lookup

CEL cannot look up arbitrary PVCs or the namespace referenced by a cluster-scoped PV. The template
rules enforce matching namespace user labels, tool claim naming, declared home-node placement,
per-user pre-binding, a local directory below the user's tool path, Retain and the login class. The
PV binding matches cluster-scoped PV requests, with policy conditions selecting the login storage
class or login label so removing labels cannot bypass validation. Cluster-scoped PV bindings cannot
use a user namespace selector.

The shipped fail-closed admission service resolves every login PVC referenced by a Pod or
StatefulSet and require its user label to equal the Namespace user label and its tool label to equal
the Pod tool label. Require its bound PV to refer to that exact namespace and claim, local path,
home-node affinity and storage class. For login PV CREATE/UPDATE, resolve claimRef.namespace and
require its `kind: user-sessions` label and matching user label. Select these PV requests by storage
class as well as labels; namespaceSelector cannot scope PVs. Refuse reassignments, cross-user
labels, cross-tool claims, unexpected node affinity or missing namespaces. Do not admit session
workloads until these lookup checks are installed. The component ships the lookup implementation and
deployment templates; certificate bootstrap, authorization of the webhook reader and platform audit
retention must be completed before rollout. Shared system namespace ownership remains with platform
hardening.

## Storage provisioning

The local login directory is provisioned on the encrypted home-node disk, excluded from every
backup, uid/gid 1000 and mode 0700 before first startup. The tmpfs option changes that directory's
filesystem, not the ownership rule. The login directory must never be populated by copying an
existing login. Kubelet fsGroup handling must not broaden it to mode 0770: the login storage driver
or node mount setup must preserve the explicit 0700 mode. Session Pods intentionally omit fsGroup to
preserve the provisioned local login directory mode. RAM emptyDirs follow the source workload
configuration and must be writable by uid 1000; projected token volumes use mode 0444 and are
mounted only into allowed containers. VERIFY emptyDir access and token readability in the target
runtime. For Codex, provision an empty uid-1000 `sessions` subdirectory before first Pod startup so
the usage subPath mount can be created. Never seed that directory with copied transcripts.

Kata virtiofs can hide the node's underlying disk or tmpfs filesystem from the guest. The entrypoint
refuses the wrong visible filesystem; a node attestation mechanism is required if virtiofs prevents
proving the selected storage mode. Never relax the guard based solely on a writable marker created
inside the Pod. Encryption, backup exclusion and mount attestation belong to the platform
provisioning and offboarding workflow.

## Controller and bootstrap exceptions

ResourceQuota, LimitRange and the registry pull Secret are bootstrap objects required by the
per-user model, additional to the allowed session workload kinds. Only GitOps and platform admins
write them. The StatefulSet controller may materialize Pods, while strict owner-reference and
pod-shape validation prevents naked or debug Pods. The Kubernetes root-CA controller creates its
public `kube-root-ca.crt` ConfigMap. VERIFY the configured controller identities; substitute through
a reviewed policy change if the distribution differs. Holders receive only read, exec/attach and
bounded StatefulSet scale permissions.
