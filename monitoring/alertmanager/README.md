# Alertmanager delivery

The organization render plugin supplies receiver references, tenant routing,
quiet-hour muting and an optional external Watchdog heartbeat.

## Interface

The parent plugin emits a Helm values fragment and reviewable ConfigMap. The
sealing helper accepts namespace, Secret name/key, public certificate and output
path; receiver values arrive through hidden input or stdin.

## Configuration

See [receiver configuration](../README.md#configuration). Every receiver Secret is
mounted by Alertmanager; email uses auth_password_file while webhook/Slack/PagerDuty
use their respective URL/API URL/routing key file fields.

## Secrets

No values ship in configuration. Seal each configured key locally and review the
result outside this repository before applying through the platform secret workflow.

## Deploy

Render first and install the complete base values plus generated routing fragment.
The sealing helper does not apply its output or contact a cluster.

## Verify

`validate-config.sh <configuration>` runs amtool when available, otherwise checks
YAML and receiver references and reports the semantic validator skipped.
`test-seal-alertmanager-urls.sh` checks no plaintext output/argv and sealer failures.
The parent unittest suite checks all receiver kinds and routing boundaries.

## Rollback

Restore the prior full Helm values and prior Secret references; preserve old
receiver endpoints until restored delivery is verified by intended recipients.

## Security notes

The plugin copies only allowlisted nonsecret receiver fields. Unknown severities
take the warning route rather than being silently dropped. Team routes preserve
critical delivery and quiet-hour warning behavior.
