#!/usr/bin/env bash
# bench-v2: make exactly ONE variant active so the other one cannot take CPU, memory or disk bandwidth (README section 6).
#   nested  -> halt nothing, start the three telco VMs, scale the direct namespace to 0
#   direct  -> halt the three telco VMs (their memory is freed), scale the direct namespace back up
#   none    -> both off (use before a fresh install)
# Run on the server:   KUBECTL="kubectl $WKC" ./switch_variant.sh direct
# The replica counts of the direct namespace are remembered in $STATE (so a UERANSIM deployment that was at 0 stays at 0).
# After `nested` the pods inside the telco cluster restart on their own; wait until telco-cp shows them Running (see EXPERIMENTS.md E0).
# WARNING: Flux reconciles the telco VMs from Git (unit telco-cluster-vms, prune: true) and would set runStrategy back to Always.
# Suspend that unit first (bench-v2/README.md section 5.3) or this script has no lasting effect.
set -euo pipefail
TARGET=${1:?nested | direct | none}
KUBECTL=${KUBECTL:-kubectl}
NS_DIRECT=${NS_DIRECT:-open5gs-direct}
NS_VMS=${NS_VMS:-telco}
VMS=${VMS:-"telco-cp telco-core telco-edge"}
STATE=${STATE:-$HOME/.bench-v2-direct-replicas}

vm_strategy() { for v in $VMS; do $KUBECTL -n "$NS_VMS" patch vm "$v" --type merge -p "{\"spec\":{\"runStrategy\":\"$1\"}}" >/dev/null; done; }
direct_down() {
  # remember replicas only if something is running (never overwrite a good state file with zeros)
  local cur; cur=$($KUBECTL -n "$NS_DIRECT" get deploy -o jsonpath='{range .items[*]}{.metadata.name}={.spec.replicas}{"\n"}{end}')
  if echo "$cur" | grep -qv '=0$'; then echo "$cur" > "$STATE"; fi
  $KUBECTL -n "$NS_DIRECT" scale deploy --all --replicas=0 >/dev/null
  $KUBECTL -n "$NS_DIRECT" wait --for=delete pod --all --timeout=300s >/dev/null 2>&1 || true
}
direct_up() {
  [ -s "$STATE" ] || { echo "no saved replica counts in $STATE; scale the direct deployments by hand"; return 1; }
  while IFS== read -r name n; do $KUBECTL -n "$NS_DIRECT" scale deploy "$name" --replicas="$n" >/dev/null; done < "$STATE"
  $KUBECTL -n "$NS_DIRECT" rollout status deploy/open5gs-mongodb --timeout=600s
}
vms_wait() {
  echo "waiting for the VMs to be Ready ..."
  for v in $VMS; do
    until $KUBECTL -n "$NS_VMS" get vmi "$v" >/dev/null 2>&1; do sleep 5; done
    $KUBECTL -n "$NS_VMS" wait vmi "$v" --for=condition=Ready --timeout=900s >/dev/null
  done
}
case "$TARGET" in
  nested) direct_down; vm_strategy Always; vms_wait; echo "nested active. Wait for the telco cluster pods (telco-cp: sudo k3s kubectl get pods -A)." ;;
  direct) vm_strategy Halted; for v in $VMS; do $KUBECTL -n "$NS_VMS" wait vmi "$v" --for=delete --timeout=300s >/dev/null 2>&1 || true; done; direct_up; echo "direct active." ;;
  none)   direct_down; vm_strategy Halted; echo "both off." ;;
  *) echo "usage: $0 nested | direct | none"; exit 2 ;;
esac
