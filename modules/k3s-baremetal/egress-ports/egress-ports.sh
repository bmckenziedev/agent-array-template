#!/usr/bin/env bash
set -euo pipefail
yes=false
[[ $# == 0 ]] || { [[ $# == 1 && $1 == --yes ]] || exit 2; yes=true; }
: "${POD_CIDR:?set POD_CIDR}" "${WAN_IF:?set WAN_IF}" "${PORTS:?set permitted return PORTS}"
[[ $PORTS =~ ^[0-9]+-[0-9]+$ ]] || exit 2
chain=AA-EGRESS-PORTS
echo "NAT-only: $POD_CIDR via $WAN_IF, source ports $PORTS"
echo "Rollback: iptables -t nat -D POSTROUTING -j $chain; iptables -t nat -F $chain; iptables -t nat -X $chain"
$yes || exit 0
[[ $(id -u) == 0 ]] || { echo 'root required' >&2; exit 1; }
ipt=(iptables -w 5 -t nat)
if ! "${ipt[@]}" -S "$chain" >/dev/null 2>&1; then "${ipt[@]}" -N "$chain"; fi
for protocol in tcp udp; do
  rule=(-s "$POD_CIDR" -o "$WAN_IF" -p "$protocol" -j MASQUERADE --to-ports "$PORTS")
  if ! "${ipt[@]}" -C "$chain" "${rule[@]}" 2>/dev/null; then "${ipt[@]}" -A "$chain" "${rule[@]}"; fi
done
if [[ $("${ipt[@]}" -S POSTROUTING | sed -n '2p') != "-A POSTROUTING -j $chain" ]]; then
  while "${ipt[@]}" -C POSTROUTING -j "$chain" 2>/dev/null; do "${ipt[@]}" -D POSTROUTING -j "$chain"; done
  "${ipt[@]}" -I POSTROUTING 1 -j "$chain"
fi
