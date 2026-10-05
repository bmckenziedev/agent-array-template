# Pod SNAT source ports

An optional NAT-only workaround constrains pod egress source ports when a stateless upstream firewall has a narrow return-port range.

## Interface

`egress-ports.sh [--yes]` prints its plan/rollback by default. Execution requires root. It owns AA-EGRESS-PORTS, adds TCP/UDP MASQUERADE rules and places its jump before distribution rules.

## Configuration

POD_CIDR, WAN_IF and PORTS are required environment settings. Use a reviewed permitted return range; no guessed interface or provider-specific defaults are supplied.

## Secrets

None.

## Deploy

Prefer widening the upstream return range. Otherwise inspect the plan and run --yes after review. Schedule reassertion through org operations if distribution rules are reordered; no timer is enabled by export scripts.

## Verify

Check chain order, TCP/UDP return traffic and low-source-port tests after node IP changes.

## Rollback

Use printed jump/chain removal commands. Disable external reassertion first.

## Security notes

No filter/UFW rules change. Repeated runs with unchanged settings are idempotent; settings changes require clearing old owned rules after review. See the module GOTCHAS.md.
