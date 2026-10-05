# Module: gpu-lanes (optional)

GPU lanes provide authenticated OpenAI-compatible llama.cpp serving behind the gateway. Promote a lane only after the factory bench gate passes for its model, flags, context and slot count.

## Interface

The module renders NVIDIA RuntimeClass and GPU device plumbing, per-node cache PVs/PVCs, lane Deployments, ClusterIP Services on port 8080, ServiceMonitors and NetworkPolicies. Enabling the module requires NVIDIA drivers and container toolkit on GPU nodes; see [NODE-SETUP.md](NODE-SETUP.md). Lane names form the LiteLLM base URL `http://<lane>.NS_MODELS.svc:8080/v1`.

## Configuration

`modules.gpu-lanes.enabled` defaults false. `cache_root` defaults `/var/lib/model-cache`, `cache_size` defaults `100Gi`, and `download_image` defaults to the digest-pinned Python 3.12 Alpine init image. `lanes` is a block list with `name`, `node`, `model_group`, HTTPS `gguf_url`, verified `gguf_sha256`, total `ctx`, `slots`, and `extra_args`. The two examples are neutral configuration scaffolding; replace URLs/checksums and add the second GPU node before enabling. No all-zero checksum is accepted. Groups must appear on a pool account for gateway rendering. CPU affinity flags remain an optional commented example, not a deployment assumption.

Global keys: `NS_MODELS`, `NS_LLM`, `NS_FACTORY`, `NS_MONITORING`, `PROJECT_NAME`, `LABEL_PREFIX`, `RUNTIME_CLASS_GPU`, `CLUSTER_DNS_IP`, `PRIVATE_CIDRS_JSON`. No extra node namespace is created; `gpu-system` is the upstream plumbing namespace. Static templates are automatically gated by the module renderer, and the plugin independently checks enablement.

## Secrets

[secrets.required.yaml](secrets.required.yaml) requires `model-server-auth:api-key` in models and llm namespaces with the same bearer value, sealed separately. The server reads a mounted file; the gateway reads a Secret environment reference. No subscription login reaches this module.

## Deploy

Prepare cache directories owned by UID/GID 1000 and sized for the configured models. Render org configuration with the shared renderer; Argo syncs `rendered/global/modules/gpu-lanes/k8s`. Local PV topology binds a cache to the configured node; pod placement uses `LABEL_PREFIX/role-gpu`, with the org GPU taint toleration. Hostname topology appears only in PV nodeAffinity, never in workload nodeSelector. PVs retain data on deletion. A checksum-specific atomic download prevents partial weights from serving.

`gpu-system` enforces privileged PSA because device plugin host sockets and DCGM host resources/SYS_ADMIN cannot pass baseline. Both warn/audit restricted. DCGM is not privileged and has only SYS_ADMIN added; the device plugin drops all capabilities. Model lanes use nonroot, read-only root filesystems, RuntimeDefault seccomp and dropped capabilities. Confirm driver access under this identity without weakening namespace policy. [POWER.md](POWER.md) describes optional host power caps.

## Verify

Run `python -m unittest discover -s modules/gpu-lanes/tests -t modules/gpu-lanes -v`, `bash modules/gpu-lanes/tests/test-grow-root-lv.sh`, and `python modules/gpu-lanes/dcgm/validate.py`. Live checks: RuntimeClass integration, one exporter per GPU node, PVC topology, download SHA256, missing bearer rejection, lane readiness, bench gate, and tool-call correctness. Hardware-dependent counters must be confirmed on the actual GPUs; unsupported counters are not zero-health evidence.

Ingress permits only LiteLLM and, when enabled, the factory namespace. Lane ServiceMonitor does not widen that boundary: direct monitoring-namespace scrapes will be denied. Supply an approved scraping proxy in the LLM/factory namespace or adapt the monitoring integration before expecting targets up. DCGM has a separate monitoring allow rule. Model download needs DNS/public 443; serving itself needs no public egress.

## Rollback

Restore the previous qualified lane definition and checksum, sync the owning Argo application, then repeat the bench gate. Disabling the module stops rendering; handle existing resources through the owning Argo pruning policy. Preserve Retain PV contents. Never reset GPUs or restart host runtimes as an automatic rollback step.

## Security notes

Default-deny boundaries protect models and plumbing. No v1 serving manifests, subscription shims, paid escalation, host power DaemonSet or workstation power control is exported. `grow-root-lv.sh` is a guarded ext4 Ubuntu LVM helper, defaults dry-run and requires `--yes` to mutate. Run only in a host maintenance window with a verified backup; unsupported layouts are refused.
