#!/usr/bin/env bash
# bench-v2: make exactly ONE Open5GS deployment active (README section 6, rule F2). The three telco VMs are NEVER stopped:
# they keep running (idle when `direct` is measured); their idle load is recorded, not removed.
#   nested  -> scale the direct namespace to 0 (the nested Open5GS inside the telco cluster is the active one)
#   direct  -> scale the direct namespace back to the replica counts it had
# Run on the server:   KUBECTL="kubectl $WKC" ./switch_variant.sh direct
# The replica counts of the direct namespace are remembered in $STATE (so a UERANSIM deployment that was at 0 stays at 0).
# The nested side is switched inside telco-cp (sudo k3s kubectl -n open5gs scale deploy --all --replicas=0|1); this script does not touch it.
set -euo pipefail
TARGET=${1:?nested | direct}
KUBECTL=${KUBECTL:-kubectl}
NS_DIRECT=${NS_DIRECT:-open5gs-direct}
STATE=${STATE:-$HOME/.bench-v2-direct-replicas}

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
case "$TARGET" in
  nested) direct_down; echo "direct namespace scaled to 0; nested is the active deployment." ;;
  direct) direct_up;   echo "direct namespace up; scale the nested Open5GS to 0 inside telco-cp if it is running." ;;
  *) echo "usage: $0 nested | direct"; exit 2 ;;
esac
