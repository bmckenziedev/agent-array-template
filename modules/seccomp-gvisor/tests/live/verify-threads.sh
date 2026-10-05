#!/usr/bin/env bash
# Inspect a pre-provisioned diagnostic pod; no resource creation or removal.
set -euo pipefail
kubeconfig= namespace= pod=
while (( $# )); do
  case "$1" in
    --kubeconfig) kubeconfig=${2:?}; shift 2 ;;
    --namespace) namespace=${2:?}; shift 2 ;;
    --pod) pod=${2:?}; shift 2 ;;
    *) echo "usage: $0 --kubeconfig FILE --namespace NS --pod POD" >&2; exit 64 ;;
  esac
done
[[ -n $kubeconfig && -n $namespace && -n $pod ]] || {
  echo "--kubeconfig, --namespace and --pod are required" >&2; exit 64;
}
out=$(kubectl --kubeconfig "$kubeconfig" -n "$namespace" logs "$pod")
expected=(
  'clone3_raw: ret=-1 errno=38 ENOSYS'
  'clone3_newuser: refused errno=38 ENOSYS'
  'pthread_create: rc=0 OK'
  'ptrace: ret=-1 errno=1 EPERM'
  'trace_bypass: impossible, PTRACE_TRACEME refused errno=1 EPERM'
  'listener_bypass: impossible, NEW_LISTENER refused errno=1 EPERM'
  'unshare_newuser: ret=-1 errno=1 EPERM'
  'python_threads: OK'
  'posix_spawn: OK'
  'python_subprocess: OK'
)
failed=0
for pattern in "${expected[@]}"; do
  if grep -Fq "$pattern" <<<"$out"; then
    printf 'PASS %s\n' "$pattern"
  else
    printf 'FAIL %s\n' "$pattern" >&2
    failed=1
  fi
done
exit "$failed"
