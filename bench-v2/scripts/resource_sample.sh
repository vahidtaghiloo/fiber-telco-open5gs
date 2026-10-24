#!/usr/bin/env bash
# bench-v2 E4 (full): CPU and memory per layer, every INTERVAL seconds. Read-only. Writes OUT/resources_<TAG>.csv with
#   ts,source,node,namespace,pod,metric,value
# source "kubelet": the kubelet's resource metrics of each node in NODES (/metrics/resource): node_cpu_usage_seconds_total,
#   node_memory_working_set_bytes, and per pod pod_cpu_usage_seconds_total, pod_memory_working_set_bytes (all namespaces, so
#   the platform, the virt-launcher pods of the telco VMs and the direct Open5GS pods are all included).
# source "host" (HOST_QEMU=1, only on the physical server): CPU ticks (utime + stime, /proc/<pid>/stat) of every qemu process,
#   named by its libvirt guest name (column "pod"), the host's total and idle ticks (/proc/stat) and CLK_TCK; this is the cost the
#   host pays for the workload node VM, including the nested telco VMs inside it.
#
#   server (workload node + host):  KUBECTL="kubectl $WKC" NODES=fiber-telco-open5gs-workload-cp-0 HOST_QEMU=1 OUT=... TAG=idle-nested DURATION=300 ./resource_sample.sh
#   telco-cp (inside the VMs):      NODES="telco-cp telco-core telco-edge" OUT=... TAG=idle-nested-inside DURATION=300 ./resource_sample.sh
set -uo pipefail
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NODES=${NODES:?set NODES to the node name(s) to sample}
OUT=${OUT:?set OUT to the output directory}
TAG=${TAG:-sample}
INTERVAL=${INTERVAL:-5}
DURATION=${DURATION:-300}
HOST_QEMU=${HOST_QEMU:-0}
mkdir -p "$OUT"
F="$OUT/resources_${TAG}.csv"
echo "ts,source,node,namespace,pod,metric,value" > "$F"
[ "$HOST_QEMU" = 1 ] && echo "$(date +%s.%3N),host,$(hostname),,,clk_tck,$(getconf CLK_TCK)" >> "$F"
end=$((SECONDS + DURATION))
n=0
while [ $SECONDS -lt $end ]; do
  ts=$(date +%s.%3N)
  for node in $NODES; do
    $KUBECTL get --raw "/api/v1/nodes/$node/proxy/metrics/resource" 2>/dev/null | awk -v ts="$ts" -v node="$node" '
      function lab(s, k) { if (match(s, "[{,]" k "=\"[^\"]*\"")) return substr(s, RSTART + length(k) + 3, RLENGTH - length(k) - 4); return "" }
      /^(node_cpu_usage_seconds_total|node_memory_working_set_bytes|pod_cpu_usage_seconds_total|pod_memory_working_set_bytes)[ {]/ {
        name = $0; sub(/[ {].*/, "", name)
        rest = $0; sub(/^[^ ]*(\{[^}]*\})? /, "", rest); split(rest, a, " ")
        print ts ",kubelet," node "," lab($0, "namespace") "," lab($0, "pod") "," name "," a[1]
      }' >> "$F"
  done
  if [ "$HOST_QEMU" = 1 ]; then
    for pid in $(pgrep -f qemu-system 2>/dev/null); do
      args=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)
      guest=$(echo "$args" | grep -o 'guest=[^, ]*' | head -1 | cut -d= -f2)
      smp=$(echo "$args" | grep -oE -- '-smp [^ ]+' | head -1 | awk '{ print $2 }' | grep -oE '^[0-9]+|cpus=[0-9]+' | head -1 | grep -oE '[0-9]+')
      ticks=$(sed 's/.*) //' /proc/$pid/stat 2>/dev/null | awk '{ print $12 + $13 }')
      # several libvirt-metal VMs share the guest name (vbmh): the label carries vCPU count and pid (workload node: 32 vCPU)
      [ -n "$ticks" ] && echo "$ts,host,$(hostname),,${guest:-qemu}_smp${smp:-NA}_pid$pid,qemu_cpu_ticks,$ticks" >> "$F"
    done
    awk -v ts="$ts" -v h="$(hostname)" '$1 == "cpu" { t = 0; for (i = 2; i <= 9; i++) t += $i; print ts ",host," h ",,,host_total_ticks," t; print ts ",host," h ",,,host_idle_ticks," ($5 + $6) }' /proc/stat >> "$F"
  fi
  n=$((n + 1))
  sleep "$INTERVAL"
done
echo "done: $n samples, $(($(wc -l < "$F") - 1)) rows in $F"
