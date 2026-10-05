# Kata runtime

Kata isolates session workloads inside hardware virtual machines. Host installation is explicit; Argo CD manages the RuntimeClass independently.

## Interface

The containerd handler is `kata`. The RuntimeClass uses `RUNTIME_CLASS_VM` and schedules only on `LABEL_PREFIX/runtime-kata: "true"` nodes. GC emits `aa_kata_gc_*` textfile metrics, without reading sandbox workload contents.

## Configuration

Org keys: `runtime_classes.vm`, `label_prefix`, and node runtime inventory. Installer environment: `KATA_VERSION` (3.32.0), `KATA_RUNTIME` (Go for 3.x, rs or go for pinned 4.2.0), `KATA_HYPERVISOR` (clh or qemu), `KATA_ROOT`, `KATA_CFG_DIR`, `KATA_CONTAINERD_DIR`, and `K3S_UNIT`. These scripts target k3s containerd v3 imports; other distributions require a reviewed integration. GC supports only the pinned Go 3.32.0 cleanup layout and refuses runtime-rs or unknown layouts.

## Secrets

None. Sandbox state can contain credentials; GC never reads persist.json contents.

## Deploy

Render the RuntimeClass, review it, and sync it through Argo CD. On each drained Linux node run `bash install-kata.sh` to view the plan, then `bash install-kata.sh --yes` to install. Downloaded bundles are checked against committed SHA256 pins before extraction. KVM, tun and the selected hypervisor's requirements must be available; zstd must be provisioned separately. After verification, the cluster inventory task applies the runtime-kata label.

`bash install-kata-gc.sh --yes` explicitly installs and starts the hourly GC timer. `kata-gc.sh` defaults to dry-run; `kata-gc.sh --yes` enables cleanup. GC never guesses a newer release layout.

## Verify

Offline: `bash platform/kata/tests/test-install-kata.sh`, `bash platform/kata/tests/test-kata-gc.sh`, and `python -m unittest discover -s platform/kata/tests -t platform/kata -v`.

Live, a platform admin supplies existing diagnostic pods to `verify-kata.sh --kubeconfig FILE --namespace NS --pod POD --privileged-pod POD --node NODE`. The ordinary probe must carry `io.katacontainers.config.hypervisor.kernel_params: kata_verify_marker=1`; the privileged diagnostic pod runs sleep with hostPID. The verifier checks differing guest kernel, ignored annotation, host disk invisibility and absence of host processes, and fails when probes return nothing. It creates no resources. VERIFY: test host device-number access, DinD builds, shared workspace binds, disk-backed emptyDir accounting, and memory reclaim on each supported release before production use.

## Rollback

Drain Kata workloads, remove the runtime node label, then run `install-kata.sh --uninstall --yes`. Versioned bundles remain for controlled rollback. Remove RuntimeClass through Argo CD only after workloads have migrated. Stop GC with `install-kata-gc.sh --uninstall --yes`.

## Security notes

No host devices are passed into privileged guests; annotations cannot override hypervisor settings. Guest seccomp stays enabled. VMM processes are charged to the pod cgroup, and disk emptyDirs stay in kubelet-accounted storage. GC requires containerd, process, socket, PID, age and host-mount checks; uncertain ownership always keeps state. Restrict runtime labels and host administration to platform admins.

Cloud alternatives (VERIFY): dedicated node pools supporting nested virtualization, Firecracker-based runtimes, or gVisor where KVM is unavailable. Confirm provider support, runtime handler, resource overhead and isolation requirements before substituting.
