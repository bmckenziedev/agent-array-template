#!/usr/bin/env bash
# Read existing diagnostic pods; a platform admin provisions probes separately.
set -euo pipefail
export MSYS_NO_PATHCONV=1
kubeconfig='' namespace='' pod='' privileged_pod='' node=''
while (( $# )); do
  case "$1" in
    --kubeconfig|--namespace|--pod|--privileged-pod|--node)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 64; }
      case "$1" in
        --kubeconfig) kubeconfig=$2 ;;
        --namespace) namespace=$2 ;;
        --pod) pod=$2 ;;
        --privileged-pod) privileged_pod=$2 ;;
        --node) node=$2 ;;
      esac
      shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 64 ;;
  esac
done
[[ -n $kubeconfig && -n $namespace && -n $pod && -n $privileged_pod && -n $node ]] || {
  echo "required: --kubeconfig FILE --namespace NS --pod POD --privileged-pod POD --node NODE" >&2; exit 64;
}
k=(kubectl --kubeconfig "$kubeconfig")
host_kernel=$("${k[@]}" get node "$node" -o jsonpath='{.status.nodeInfo.kernelVersion}')
guest_kernel=$("${k[@]}" -n "$namespace" exec "$pod" -- uname -r)
[[ -n $host_kernel && -n $guest_kernel && $host_kernel != "$guest_kernel" ]] || {
  echo "FAIL: missing or matching guest/host kernel" >&2; exit 1;
}
cmdline=$("${k[@]}" -n "$namespace" exec "$pod" -- cat /proc/cmdline)
[[ -n $cmdline && $cmdline != *kata_verify_marker=1* ]] || {
  echo "FAIL: missing cmdline or hypervisor annotation reached guest" >&2; exit 1;
}
devices=$("${k[@]}" -n "$namespace" exec "$privileged_pod" -- ls /dev)
grep -qw null <<<"$devices" || { echo "FAIL: device probe did not run" >&2; exit 1; }
if grep -Eq '^(nvme|sd[a-z]|hd[a-z]|xvd|md[0-9]|dm-)' <<<"$devices"; then
  echo "FAIL: host disk device exposed" >&2; exit 1
fi
processes=$("${k[@]}" -n "$namespace" exec "$privileged_pod" -- ps)
grep -qw sleep <<<"$processes" || { echo "FAIL: process probe did not run" >&2; exit 1; }
if grep -Eq 'k3s|kubelet|containerd|etcd|sshd|systemd' <<<"$processes"; then
  echo "FAIL: host process exposed" >&2; exit 1
fi
echo "PASS: guest kernel, annotation boundary, host disk and process isolation"
