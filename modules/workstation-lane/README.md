# Module: workstation-lane (optional)
Optional experimental volunteer GPU workstation capacity, constrained to a configured IANA time-zone window.

## Interface
lane.config.example.json is an example only: copy outside version control as lane.config.json. Run-Lane.ps1 supervises Python in the interactive user session. Configured model/worker executables are supervised foreground children. The device Role grants only pods get/list and pods/exec create in NS_FACTORY.

## Configuration
Global namespace, identity, runtime, image and cluster network keys follow the organisation configuration.

- `enabled` â†’ `M_WORKSTATION_LANE_ENABLED`, default `false`.
- `window_start` â†’ `M_WORKSTATION_LANE_WINDOW_START`, default `19:00`.
- `window_stop` â†’ `M_WORKSTATION_LANE_WINDOW_STOP`, default `08:00`.
- `timezone` â†’ `M_WORKSTATION_LANE_TIMEZONE`, default `utc`.
- `drain_minutes` â†’ `M_WORKSTATION_LANE_DRAIN_MINUTES`, default `15`.

## Secrets
Names and key names only; see [secrets.required.yaml](secrets.required.yaml). No values are committed.
For configurable bot/identity Secret names, provisioning must use the configured names rather than the defaults.

## Deploy
Enable and render config.json. Use PowerShell 7 on .NET with TryConvertIanaIdToWindowsId support and Python with IANA tzdata. Provision one short-lived device ServiceAccount kubeconfig per machine; never an admin kubeconfig. Install the interactive Task Scheduler entry using Install-Schedule.ps1 -Yes -Config MACHINE_CONFIG -Schedule RENDERED_CONFIG.

## Verify
Run `python -m unittest discover -s modules/workstation-lane/tests -t modules/workstation-lane` offline.
Render tests use an unmodified canonical fixture; modules are enabled only in an in-memory copy.
Live checks require a platform admin and are never part of offline tests.

## Rollback
Unregister VolunteerFactoryLane, stop the foreground supervisor and revoke the device credential and RoleBinding.

## Security notes
The worker stops on OS lock, parent exit, child failure or window close. Work starts only before the drain interval. The target design is a device-token pull-only factory worker API; the transitional exec transport can run arbitrary commands in any pod in NS_FACTORY, exposing factory data and mounted credentials. It is suitable only for trusted volunteers and a dedicated factory namespace. The external factory-worker executable must consume FACTORY_PULL_COMMAND_JSON, implement leasing/idempotent result publication and exit on termination; no claim is made that this repository supplies that executable.

The supervisor writes FACTORY_DRAIN_FILE at the drain boundary. The worker must stop claiming leases then finish current work; all descendants are killed at window close. Windows job objects enforce parent ownership even when the supervisor crashes. Run-Lane.ps1 previews by default; -Yes starts processes. Install pinned runtime requirements in a dedicated workstation virtual environment.

The transitional client forces the POST/SPDY exec transport using KUBECTL_REMOTE_COMMAND_WEBSOCKETS=false, preserving the Role's create-only pods/exec grant. Verify compatibility with the deployed kubectl and API server. See [kubectl exec transport implementation](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/exec/exec.go).
