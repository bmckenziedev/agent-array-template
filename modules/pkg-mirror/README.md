# Module: pkg-mirror (optional)

Verdaccio and devpi provide read-only npm and PyPI caches. Task testers download dependencies through these mirrors while retaining no direct Internet access.

## Interface

The module creates `<project>-pkg-mirror`, ClusterIP Services `npm:4873` and `pypi:3141`. The PyPI index is `/root/pypi/+simple/`. Ingress admits only `session-job` pods in the configured session-jobs namespace. Namespace and default-deny policy belong to this module.

## Configuration

`modules.pkg-mirror.enabled` defaults to false. Defaults declared in `org.component.defaults.yaml`: `npm_upstream` (`https://registry.npmjs.org/`), `pypi_upstream` (`https://pypi.org/simple/`), `storage` (`30Gi` per PVC), `upstream_cidrs` (`["0.0.0.0/0"]`). Keys are `M_PKG_MIRROR_NPM_UPSTREAM`, `M_PKG_MIRROR_PYPI_UPSTREAM`, and `M_PKG_MIRROR_STORAGE`. Templates also consume project, worker label, VM runtime, Kubernetes version and storage-class keys.

HTTPS upstreams must have no embedded credentials. With Artifactory or Nexus, set both URLs to the organisational read-only repositories, configure read-only anonymous access or an external authenticated egress gateway, and narrow `upstream_cidrs`. The default public policy excludes private, node, mesh and metadata networks. A private registry needs an explicitly reviewed CIDR allowance and corresponding change to the exclusion policy; no private access is granted implicitly. An existing Artifactory/Nexus mirror can replace this module by supplying those endpoints to session-jobs and matching its egress policy.

## Secrets

No Secrets are consumed. See `secrets.required.yaml`. devpi generates a random root password at first initialization and discards it; registration, uploads and index modification remain unavailable.

## Deploy

Enable the module, render the org configuration, and manually sync `rendered/global/modules/pkg-mirror/`. Both servers use the configured VM runtime. Images and the devpi dependency closure are digest/hash pinned. First startup downloads only locked binary wheels from the configured PyPI upstream. Re-lock explicitly with `python modules/pkg-mirror/lock_devpi.py --yes [version]`; `--check --yes` checks without rewriting (exit 1 for drift).

## Verify

Run `python -m unittest discover -s modules/pkg-mirror/tests -t modules/pkg-mirror`. Live checks are separate and were not run: `bash modules/pkg-mirror/verify/verify.sh <kubeconfig> <session-jobs-namespace> <admitted-task-pod> <mirror-namespace>`. The tester must contain node and pip. The probes check installs, replay, direct-egress denial and write refusal. Platform admins should also check ingress denial from another namespace and mirror egress denial to API/node/metadata addresses. Standard NetworkPolicy cannot enforce hostname filtering: the public CIDR rule permits public HTTPS; upstream configuration limits normal requests, while a compromised mirror still needs an FQDN-capable egress gateway for hostname enforcement.

## Rollback

Revert rendered configuration and manually sync. Preserve the PVCs to retain cache data. An upstream change affects new cache fetches; replace cache PVCs if old upstream content must be discarded. Removing the namespace deletes its claims, so backup policy must be reviewed before removal.

## Security notes

Mirrors are the single public package egress point. Consumers must retain default-deny egress and use only these Services. Publish, unpublish, registration and devpi mutation are denied. Both servers have read-only root filesystems, no API token, non-root users, dropped capabilities and RuntimeDefault seccomp. devpi code is mounted read-only after a hash-verified binary-only install. No request-body audit middleware is enabled in Verdaccio.
