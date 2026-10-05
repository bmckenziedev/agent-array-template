# Portal end-to-end harness

The Docker harness exercises real panel authentication, directory mapping, task upload, key minting, callbacks, verification and bundle delivery without a cluster or real keys. It also runs the real module orchestrator and tester in separate Docker PID namespaces for pass and repair scenarios. It rebuilds the source harness around two teams and local model groups. Kubernetes scheduling, model inference and package registry upstreams are stand-ins. The tester uses a stdlib-only fixture with no package downloads.

## Interface

`python portal/e2e/run_e2e.py` builds and runs a foreground container and returns zero only after every scenario passes. `--no-build` reuses the local image. `python portal/e2e/run_pipeline.py --scenario repair` reruns only the real pipeline against an existing image. All Docker clients run in the foreground, are tracked and joined; temporary containers, named volumes and internal networks are cleaned in finally blocks. No host mounts are used.

## Configuration

Runtime-generated RSA keys and a local TLS issuer are created only in a temporary directory. Fake users belong to payments and platform; group claims must match directory membership. Scenarios cover successful delivery, cross-team denial, unknown users and suspended users, plus disabled-module responses. Model groups are `local-coder` and `local-coder-small`.

## Secrets

No external secrets. Synthetic task and mint credentials exist only in the test process. RSA private material is generated at runtime and never committed.

## Deploy

Run from the repository root on a workstation with Docker. Building needs access to the pinned Python image and the hash-locked Python wheels; the fast API scenarios use `--network none`; actual pipeline containers share a temporary `--internal` network admitting only the model stand-in, with no internet route. This is an optional harness and is outside all `tests/` directories and the unit discovery suite. CI may opt in later.

## Verify

Run `python portal/e2e/run_e2e.py`. The real panel executes in-process ASGI requests, the local issuer and fake LiteLLM bind only loopback ephemeral ports, and all servers stop before return. Pass and repair pipeline scenarios use real `orchestrator.py`, real `tester.py`, nonce-bound filesystem IPC, separate process namespaces and read-only workspace/testbox mounts in the opposing container. Generated test code asserts no task, model or mint credential appears in its environment. The harness does not validate Kubernetes admission, Kata/gVisor or real proxy login flows; the module offline suites and deployment verification cover those boundaries.

## Rollback

Delete the local `agent-array-portal-e2e:local` image if rebuilding is necessary.

## Security notes

The fake kubelet receives only the per-task callback/model credentials. The real tester container receives no model or callback credential; only the model and orchestrator containers receive a generated model-test key. Mint requests are checked for user/team/model/budget attribution. The generated code test process has a credential-free environment. No retired subscription routing or external vendor model groups are included.
