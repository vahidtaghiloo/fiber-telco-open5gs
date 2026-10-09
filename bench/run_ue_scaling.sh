#!/usr/bin/env bash
# Control-plane benchmark: start N UERANSIM UEs, REPS times per N, and save the UE log of each run.
# The UE pod is restarted for every run (stop, settle, start with `ue -n N`).
#
# usage (inside telco-cp):
#   OUT=~/bench-results/nested/ue ./run_ue_scaling.sh "1 10 50 100" 5
# baseline on the workload cluster (server):
#   KUBECTL="kubectl $WKC" NS=open5gs-direct OUT=$HOME/bench-results/direct/ue ./run_ue_scaling.sh "1 10 50 100" 5
#
# Prerequisites: subscribers provisioned for max(N) (provision_subscribers.sh), and the ueransim
# HelmRelease suspended so Flux does not undo the patch (see BENCHMARKS.md).
set -euo pipefail
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
OUT=${OUT:?set OUT to the output directory}
DEP=${DEP:-ueransim-ueransim-gnb-ues}
SELECTOR=${SELECTOR:-app.kubernetes.io/component=ues}
LIST=${1:-"1 10 50"}
REPS=${2:-5}
TIMEOUT=${TIMEOUT:-240}     # seconds to wait for all N PDU sessions
SETTLE=${SETTLE:-15}        # seconds between runs, lets the AMF drop the old UE contexts
mkdir -p "$OUT"

for n in $LIST; do
  for r in $(seq 1 "$REPS"); do
    tag=$(printf 'N%03d_r%02d' "$n" "$r")
    echo "== N=$n run $r/$REPS"
    $KUBECTL -n "$NS" scale deploy/"$DEP" --replicas=0 >/dev/null
    $KUBECTL -n "$NS" wait --for=delete pod -l "$SELECTOR" --timeout=90s >/dev/null 2>&1 || true
    sleep "$SETTLE"
    $KUBECTL -n "$NS" patch deploy/"$DEP" --type=json \
      -p "[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/args/2\",\"value\":\"$n\"}]" >/dev/null
    $KUBECTL -n "$NS" scale deploy/"$DEP" --replicas=1 >/dev/null

    pod=""
    for _ in $(seq 1 60); do
      pod=$($KUBECTL -n "$NS" get pods -l "$SELECTOR" -o name 2>/dev/null | head -1 || true)
      [ -n "$pod" ] && break
      sleep 1
    done
    ok=0
    for _ in $(seq 1 "$TIMEOUT"); do
      ok=$($KUBECTL -n "$NS" logs "$pod" 2>/dev/null | grep -c "PDU Session establishment is successful" || true)
      [ "${ok:-0}" -ge "$n" ] && break
      sleep 1
    done
    $KUBECTL -n "$NS" logs "$pod" > "$OUT/ue_$tag.log" 2>&1 || true
    echo "   $ok/$n PDU sessions -> $OUT/ue_$tag.log"
  done
done
echo "finished; leave the deployment at a small N again if you need the demo state back (see BENCHMARKS.md)"
