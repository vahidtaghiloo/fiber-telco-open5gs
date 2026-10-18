#!/usr/bin/env bash
# bench-v2 (E4): samples the kubelet's cAdvisor counters of every container in one namespace, every INTERVAL seconds.
# Read-only. Writes OUT/cadvisor_<TAG>.csv with: ts,node,namespace,pod,container,metric,value
# Metrics: container_cpu_usage_seconds_total, container_cpu_cfs_periods_total, container_cpu_cfs_throttled_periods_total,
#          container_cpu_cfs_throttled_seconds_total, container_memory_working_set_bytes  (counters are cumulative per container)
#
#   nested (telco-cp):  NODES="telco-core telco-edge" NS=open5gs OUT=... TAG=pr1000 ./cadvisor_sample.sh
#   direct (server):    KUBECTL="kubectl $WKC" NODES=fiber-telco-open5gs-workload-cp-0 NS=open5gs-direct OUT=... TAG=pr1000 ./cadvisor_sample.sh
set -uo pipefail
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NODES=${NODES:?set NODES to the node name(s) to sample}
NS=${NS:?set NS to the namespace}
OUT=${OUT:?set OUT to the output directory}
TAG=${TAG:-sample}
INTERVAL=${INTERVAL:-5}
DURATION=${DURATION:-180}
mkdir -p "$OUT"
F="$OUT/cadvisor_${TAG}.csv"
echo "ts,node,namespace,pod,container,metric,value" > "$F"
end=$((SECONDS + DURATION))
n=0
while [ $SECONDS -lt $end ]; do
  ts=$(date +%s.%3N)
  for node in $NODES; do
    $KUBECTL get --raw "/api/v1/nodes/$node/proxy/metrics/cadvisor" 2>/dev/null | awk -v ts="$ts" -v node="$node" -v ns="$NS" '
      function lab(s, k) { if (match(s, "[{,]" k "=\"[^\"]*\"")) return substr(s, RSTART + length(k) + 3, RLENGTH - length(k) - 4); return "" }
      /^container_(cpu_usage_seconds_total|cpu_cfs_periods_total|cpu_cfs_throttled_periods_total|cpu_cfs_throttled_seconds_total|memory_working_set_bytes)\{/ {
        if (lab($0, "namespace") != ns) next
        c = lab($0, "container"); if (c == "" || c == "POD") next
        name = substr($0, 1, index($0, "{") - 1)
        rest = substr($0, index($0, "} ") + 2); split(rest, a, " ")
        print ts "," node "," ns "," lab($0, "pod") "," c "," name "," a[1]
      }' >> "$F"
  done
  n=$((n + 1))
  sleep "$INTERVAL"
done
echo "done: $n samples, $(($(wc -l < "$F") - 1)) rows in $F"
