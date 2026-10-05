# Module: seccomp-gvisor (optional)

This opt-in module adapts a reference containerd RuntimeDefault profile for session-jobs using gVisor with OCI seccomp enabled. It changes clone3 to TRACE while denying ptrace and seccomp listener creation, so glibc can fall back to clone without opening namespace creation bypasses.

## Interface

`seccomp_profile.py build|check|diff` builds or checks JSON profiles. `install-profile.sh` checks the node by default; `--yes` installs the canonical profile under `profiles/seccomp-gvisor/runtime-default-clone3-enosys.json`. Optional explicit patches integrate that exact path with session-jobs templates, admission and installer preflight.

## Configuration

Enable `modules.seccomp-gvisor.enabled` in org.yaml only together with reviewed session-jobs gVisor integration. `KUBELET_ROOT` defaults to `/var/lib/kubelet`. No additional placeholder keys are required. The module does not silently patch another component or enable gVisor by default.

## Secrets

None. Probe pods contain no credentials and disable service-account token mounting.

## Deploy

Run `python modules/seccomp-gvisor/seccomp_profile.py check`, then on each drained gVisor node run `bash install-profile.sh --yes`. Install before runtime labeling. Review patches against the actual session-jobs contract with `python patches/make-patches.py --target <session-jobs-source>`; target files remain untouched. The committed patches demonstrate the minimal integration using module-owned fixtures. Apply reviewed changes through the session-jobs owner and render to Argo. Enable runsc `oci-seccomp=true`; without it guest filtering is not enforced.

## Verify

Offline: `python -m unittest discover -s modules/seccomp-gvisor/tests -t modules/seccomp-gvisor -v`, `python modules/seccomp-gvisor/tests/check_admission_patch.py --offline`, and `python modules/seccomp-gvisor/patches/make-patches.py --check`.

Live: `tests/check_admission_patch.py --kubeconfig FILE --rendered JOB.yaml` sends positive Job and Pod cases before negative server dry-runs against policies already installed by the platform admin. It creates no resources. Probe and sweep tools under `tests/live/` are diagnostic tools only; use dedicated credential-free pods. VERIFY runsc clone3 ENOSYS behavior, threading, ptrace denial, listener denial and namespace-flag rejection after every runtime upgrade.

## Rollback

Drain affected workloads before reverting both exact-profile admission and template changes together. `install-profile.sh --uninstall --yes` removes only the module's profile; new pods referencing a removed file fail to start. Running containers keep their existing filters.

## Security notes

TRACE is safe only while ptrace and seccomp listener installation remain denied. The checker rejects weaker defaults, new allowed syscalls, changed architectures and unsafe TRACE combinations. The reference is version-specific; regenerate and review after containerd changes. Optional-field CEL reads are guarded with has(), and bindings must select the session-jobs namespace plus opted-in workload label. Plain busybox Pod and Deployment shapes outside the module selector must remain admitted.
