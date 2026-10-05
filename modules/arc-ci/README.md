# Module: arc-ci (optional)
Optional ARC controller and light gVisor / heavy Kata runner scale sets with bounded resources and admission controls.

## Interface
Helm chart 0.15.0 values, explicit runner ServiceAccounts, quotas, PriorityClass, network rules and a has()-guarded admission policy. Smoke workflow is an example only.

## Configuration
Global namespace, identity, runtime, image and cluster network keys follow the organisation configuration.

- `enabled` â†’ `M_ARC_CI_ENABLED`, default `false`.
- `runner_light` â†’ `M_ARC_CI_RUNNER_LIGHT`, default `arc-light`.
- `runner_heavy` â†’ `M_ARC_CI_RUNNER_HEAVY`, default `arc-heavy`.
- `trusted_group` â†’ `M_ARC_CI_TRUSTED_GROUP`, default `trusted-builds`.
- `max_light` â†’ `M_ARC_CI_MAX_LIGHT`, default `4`.
- `max_heavy` â†’ `M_ARC_CI_MAX_HEAVY`, default `3`.

## Secrets
Names and key names only; see [secrets.required.yaml](secrets.required.yaml). No values are committed.

## Deploy
Enable, render, provision a GitHub App in the three ARC namespaces and create a selected-repositories trusted-builds runner group with public repositories disallowed. Apply namespace baselines and inspect controller/listener service accounts before install.sh --yes RENDERED_HELM_DIR. Use ARC_RENDERED_DIR for live admission checks; check_admission.py defaults to server dry-run and --live explicitly opts into temporary mutations.

## Verify
Run `python -m unittest discover -s modules/arc-ci/tests -t modules/arc-ci` offline.
Render tests use an unmodified canonical fixture; modules are enabled only in an in-memory copy.
Live checks require a platform admin and are never part of offline tests.

## Rollback
Pause scale sets, remove the admission binding only after isolating the namespaces, then roll back with a full explicit values file. Never use --reuse-values.

## Security notes
agent-array-arc-systems enforces restricted PSA; agent-array-arc-runners baseline allows upstream runner image behaviour; agent-array-arc-heavy privileged admits only the Kata dind native sidecar through the custom policy. Both relaxed namespaces audit/warn restricted. Privileged dind is confined to the microVM, with no hostPath, host namespaces or runtime overrides. The runtime must use privileged_without_host_devices. Docker storage uses a guest-loop-mounted ext4 image; the socket uses memory emptyDir to avoid separate virtio-fs socket mounts. No chart containerMode is combined with the explicit dind template. GitHub App secrets never reach runner pods: only controller-created own-name JIT secret references are admitted. Review namespace permissions and RuntimeClass overhead before allowing trusted builds.

Set controller_digest / M_ARC_CI_CONTROLLER_DIGEST to a verified 64-character SHA-256 digest for chart release 0.15.0. The default REQUIRED marker deliberately prevents installation; the pinned source has no controller digest to carry over. Runner and DinD images retain the source digests. PSA version uses latest rather than the distribution build string, which is not a valid PSA version selector.

Controller/listener security contexts are explicit to meet restricted PSA. Light runners retain upstream sudo support inside gVisor; setting allowPrivilegeEscalation=false would intentionally disable those build steps. Platform admins must verify the configured gVisor allow-suid setting and chart-rendered listener contexts.

The install wrapper takes `--yes <rendered Helm directory> <project name>`; namespace
suffixes are arc-systems, arc-runners and arc-heavy, prefixed by that project name.
