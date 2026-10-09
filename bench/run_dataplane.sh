#!/usr/bin/env bash
# Data-plane benchmark. Two paths to the same iperf3 server pod (on telco-core):
#   pod : plain pod-to-pod from the netshoot-edge pod  (no 5G in the path: flannel over net1 only)
#   ue  : from inside the UE pod's network namespace, bound to uesimtun0 (UE -> gNB -> UPF -> server)
# Output: files iperf_<path>_<proto>_<tag>.json (RUNS concatenated iperf3 JSON objects) and ping_<path>.txt
#
# usage (inside telco-cp):  OUT=~/bench-results/nested/dp RUNS=10 DURATION=20 ./run_dataplane.sh
# baseline (server):        KUBECTL="kubectl $WKC" UE_NS=open5gs-direct OUT=$HOME/bench-results/direct/dp ./run_dataplane.sh
# Prerequisite: manifests/bench-ns-and-servers.yaml applied in the SAME cluster as the UE pod.
set -euo pipefail
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
UE_NS=${UE_NS:-open5gs}
UE_SELECTOR=${UE_SELECTOR:-app.kubernetes.io/component=ues}
UE_CONTAINER=${UE_CONTAINER:-ues}
OUT=${OUT:?set OUT to the output directory}
RUNS=${RUNS:-10}
DURATION=${DURATION:-20}
mkdir -p "$OUT"

SRV=$($KUBECTL -n bench get pod -l app=iperf-server -o jsonpath='{.items[0].status.podIP}')
[ -n "$SRV" ] || { echo "iperf-server pod not found (apply manifests/bench-ns-and-servers.yaml)"; exit 1; }
echo "iperf3 server pod IP: $SRV"

# $1 = path name (pod|ue), $2 = extra iperf3 client flags, $3 = ping interface flags, $4 = shell prelude
suite() {
cat <<INNER
$4
SRV=$SRV
run() { name=\$1; shift; echo "@@@ \$name"; i=0
  while [ \$i -lt $RUNS ]; do iperf3 -c \$SRV $2 -t $DURATION -J "\$@" || true; i=\$((i+1)); sleep 3; done; }
run iperf_$1_tcp_P1.json -P 1
run iperf_$1_tcp_P4.json -P 4
for b in 50M 100M 200M; do run iperf_$1_udp_b\$b.json -u -b \$b; done
echo "@@@ ping_$1.txt"
ping -c 200 -i 0.2 $3 \$SRV
INNER
}

split_out() { awk -v out="$OUT" '/^@@@ /{f=$2; next} f{print > (out "/" f)}'; }

echo "== path: pod -> server (baseline without 5G)"
suite pod "" "" "" > /tmp/suite_pod.sh
$KUBECTL -n bench exec -i deploy/netshoot-edge -- sh -s < /tmp/suite_pod.sh | split_out

echo "== path: UE (uesimtun0) -> UPF -> server"
UEPOD=$($KUBECTL -n "$UE_NS" get pods -l "$UE_SELECTOR" -o name | head -1 | sed 's#pod/##')
[ -n "$UEPOD" ] || { echo "UE pod not found"; exit 1; }
suite ue '-B $TUNIP --bind-dev uesimtun0' '-I uesimtun0' \
  'TUNIP=$(ip -4 -o addr show uesimtun0 | awk "{print \$4}" | cut -d/ -f1); echo "tun ip: $TUNIP" >&2' > /tmp/suite_ue.sh
$KUBECTL -n "$UE_NS" debug -i --quiet "$UEPOD" --image=docker.io/nicolaka/netshoot:latest \
  --target="$UE_CONTAINER" --profile=sysadmin -- sh -s < /tmp/suite_ue.sh | split_out

ls -l "$OUT"
