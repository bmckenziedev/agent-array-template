# GPU node setup

Platform admins install a supported NVIDIA driver and NVIDIA Container Toolkit on each Linux GPU node. Confirm driver compatibility with the pinned CUDA server image and DCGM release. Register the NVIDIA runtime handler with containerd according to the cluster distribution and toolkit documentation; retain the standard runtime as the default.

The cluster task applies the organisation GPU role label and GPU NoSchedule taint. The configured `RUNTIME_CLASS_GPU` names the NVIDIA RuntimeClass. Verify `nvidia-smi`, runtime integration and device-plugin readiness before scheduling a lane. Do not install competing device plugins or exporters.

Create `/var/lib/model-cache` (or configured `cache_root`) on each GPU node with owner UID/GID 1000, traversal access and enough capacity for both existing and replacement GGUF files. Local PV mounts require host directory preparation; fsGroup alone is not a substitute. Download from a reviewed HTTPS URL and independently verify its SHA256. Model credentials are unnecessary for the public example; private downloads require a separately reviewed authentication integration.

Run the module's offline tests before server dry-run. Live qualification must prove nonroot device access, required CUDA libraries, bearer authentication, memory capacity, context/slot behavior and the factory bench gate. Preserve driver/runtime packages and manifests for rollback. Host filesystem growth is optional; [grow-root-lv.sh](grow-root-lv.sh) refuses unsupported root layouts and defaults to a dry run.
