# Module: session-jobs (optional)

Ephemeral task Jobs isolate generated-code verification from scoped model credentials. Each task has a bounded lifetime, a private workspace, and a credential-free tester sidecar.

## Interface

The `session-job-template` ConfigMap in the portal namespace exposes `task-job.template.yaml`. The panel dispatches Jobs with user, team, account, and task labels and injects TASK_TOKEN, LITELLM_KEY and TASK_MODELS into the session container only. The runtime image fetches input and posts results to the panel internal listener on 8081. Tester IPC uses request/result files and unpredictable nonces. The orchestrator returns 0 for verified results, 3 for needs-review, and 1 when no model call succeeds.

## Configuration

`modules.session-jobs.enabled` defaults to false. `modules.session-jobs.runtime` defaults to `kata`; `gvisor` requires the seccomp-gvisor module. IMAGE_SESSION_RUNNER selects the digest-pinned image; NS_SESSION_JOBS selects the existing hardening-owned namespace. Runtime class names come from RUNTIME_CLASS_VM and RUNTIME_CLASS_GVISOR. Job model groups come exclusively from the minted key entitlement in TASK_MODELS. The package mirror module should be enabled for projects requiring dependency installs; projects with preinstalled dependencies can run without it.

## Secrets

No standing Secret is required by this module. The panel supplies ephemeral per-task credentials as literal environment values in the Job, using its mint key. They are never present in tester env or mounts. See secrets.required.yaml. Kubernetes users with Job read access can see session credentials; restrict that access to platform administrators and the panel.

## Deploy

Render the organization configuration and sync the rendered module manifests through GitOps. Build from the repository root with `docker build -f modules/session-jobs/image/Dockerfile .`. The Namespace is created by platform/hardening. The panel ServiceAccount receives Jobs-only rights in the task namespace.

## Verify

Run `python -m unittest discover -s modules/session-jobs/tests -t modules/session-jobs` after installing requirements-test.txt in a temporary venv. Linux is required for real process-group isolation tests. Before enabling admission bindings, a platform admin must run server dry runs for a valid template Job and a realistic controller-owned Pod, then negative cases: missing runtime, credential-bearing tester, mutable image, hostPath, and direct Pod creation. A normal Pod in another namespace must remain accepted. Native sidecars require Kubernetes 1.29 or newer; admission policies require 1.30 or newer. Confirm Job-controller identity in the target distribution.

## Rollback

Disable new dispatch in the panel, drain existing Jobs, and disable the module. Remove bindings before changing admission policy runtime settings. Revert image and template together.

## Security notes

The session process never runs uploaded or generated code. Tester runs an immutable copy with a minimal environment, distinct uid and PID namespace; all child processes are killed at completion. The tester cannot alter the bundled workspace. Verification requires positive test-count evidence and affirmative review; missing tests or reviewers yield needs-review. Admission checks constrain commands, images, mounts, env sources, and sandbox settings. Egress is limited to DNS, LiteLLM, package mirrors and the panel internal callback required by the pipeline. No internet or Kubernetes API access is granted to task Pods. Audit output omits prompts, test output and credentials; verification artifacts may contain test output and remain scoped to the task.

### Admission positive dry runs

The following ordinary workloads deliberately omit optional initContainers, ports,
and volumes. Both must pass server admission outside the task namespace. Use a
platform-admin-selected ordinary namespace named `validation` that exists and is
outside NS_SESSION_JOBS. Commands validate only; they do not create resources.

```bash
kubectl --kubeconfig ~/.kube/example-cluster.yaml --dry-run=server create -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: ordinary-positive
  namespace: validation
spec:
  containers:
    - name: busybox
      image: busybox:1.36
      command: [sleep, "60"]
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        allowPrivilegeEscalation: false
        capabilities:
          drop: [ALL]
        seccompProfile:
          type: RuntimeDefault
YAML

kubectl --kubeconfig ~/.kube/example-cluster.yaml --dry-run=server create -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ordinary-deployment-positive
  namespace: validation
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ordinary-positive
  template:
    metadata:
      labels:
        app: ordinary-positive
    spec:
      containers:
        - name: busybox
          image: busybox:1.36
          command: [sleep, "60"]
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
            seccompProfile:
              type: RuntimeDefault
YAML
```

Repeat the Pod example with its namespace changed to the rendered NS_SESSION_JOBS
value. This direct Pod request must be rejected: only a controller-owned Pod from
a permitted task Job is admitted there. The ordinary namespace examples ensure
optional-field guards do not accidentally block unrelated workloads.
