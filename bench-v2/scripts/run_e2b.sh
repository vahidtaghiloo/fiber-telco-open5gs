#!/usr/bin/env bash
# bench-v2 E2b: user-plane throughput with the UPF as the limit. The first E2 run showed that the generator, not the UPF, limits
# throughput (the UPF used at most 0.55 of its 1.5 CPU). Here the UPF gets SMALLER limits and the generator a larger budget,
# the same in both variants:
#   UPF CPU            UPF_CPUS (default 250m 500m 1000m; restored to BASE_UPF_CPU at the end)
#   PacketRusher pod   packetrusher PR_CPU 1750m + tools (iperf3 client) TOOLS_CPU 750m = 2.5 CPU
#   UERANSIM gNB       scaled to 0 during the run (restored at the end)
# Budget on telco-edge (README section 4, at most 3.5 CPU of pods): UPF <= 1.0 + 2.5 = 3.5.
# Per UPF value, REPS repetitions of `run_packetrusher.sh dataplane` with paths "pod" (no 5G) and "pr" (tunnel + UPF),
# TCP with 1 and 4 streams and 50 pings each (no UDP: its sender is CPU-bound, see the E2 report). Sampler as in E2.
# Results: $OUT/upf<cpu>/rep<k>/iperf_*.json, ping_*.txt, cadvisor_e2.csv, run.log
#
#   nested (telco-cp):  OUT=~/bench-v2-results/e2b/nested ./run_e2b.sh
#   direct (server):    KUBECTL="kubectl $WKC" NS=open5gs-direct NODESEL=0 NODES=fiber-telco-open5gs-workload-cp-0 OUT=$HOME/bench-v2-results/e2b/direct ./run_e2b.sh
set -uo pipefail
OUT=${OUT:?set OUT to the output directory of the variant}
REPS=${REPS:-3}
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
NODESEL=${NODESEL:-1}
NODES=${NODES:-"telco-cp telco-core telco-edge"}
UPF_CPUS=${UPF_CPUS:-"250m 500m 1000m"}
BASE_UPF_CPU=${BASE_UPF_CPU:-1500m}
PR_CPU=${PR_CPU:-1750m}
TOOLS_CPU=${TOOLS_CPU:-750m}
DURATION=${DURATION:-20}
GNB_DEP=${GNB_DEP:-ueransim-ueransim-gnb}
HERE=$(cd "$(dirname "$0")" && pwd)
K="$KUBECTL -n $NS"
mkdir -p "$OUT"

set_upf() {   # $1 = CPU (requests = limits, memory unchanged), then restart the SMF so it re-associates with the new UPF pod
  $K set resources deploy/open5gs-upf -c open5gs-upf --requests="cpu=$1,memory=2Gi" --limits="cpu=$1,memory=2Gi" >/dev/null
  $K rollout status deploy/open5gs-upf --timeout=300s >/dev/null || echo "    WARNING: open5gs-upf not ready after 300 s"
  $K rollout restart deploy/open5gs-smf >/dev/null
  $K rollout status deploy/open5gs-smf --timeout=300s >/dev/null || echo "    WARNING: open5gs-smf not ready after 300 s"
  sleep 30
  echo "    UPF limit now: $($K get deploy open5gs-upf -o jsonpath='{.spec.template.spec.containers[?(@.name=="open5gs-upf")].resources.limits.cpu}')"
}

restore() {
  echo "--- restoring: UPF $BASE_UPF_CPU, UERANSIM gNB 1 replica"
  set_upf "$BASE_UPF_CPU"
  $K scale deploy/"$GNB_DEP" --replicas=1 >/dev/null 2>&1 || true
}
trap restore EXIT

summary() {   # $1 = repetition folder: one line per iperf3 file
  python3 - "$1" <<'PY'
import glob, json, os, sys
for f in sorted(glob.glob(os.path.join(sys.argv[1], "iperf_*.json"))):
    name = os.path.basename(f)[6:-5]
    try:
        o = json.load(open(f))
    except Exception:
        print(f"    {name:12s} UNREADABLE"); continue
    if "error" in o:
        print(f"    {name:12s} ERROR: {o['error']}"); continue
    e = o["end"]
    print(f"    {name:12s} {e['sum_received']['bits_per_second'] / 1e6:7.0f} Mbit/s   retransmits {e['sum_sent'].get('retransmits', 0)}")
PY
}

echo "--- UERANSIM gNB to 0 (frees 0.375 CPU on the edge for the larger generator)"
$K scale deploy/"$GNB_DEP" --replicas=0 >/dev/null
sleep 20
for cpu in $UPF_CPUS; do
  echo "##### UPF limit $cpu ($(date '+%H:%M:%S'))"
  set_upf "$cpu"
  for k in $(seq 1 "$REPS"); do
    base="$OUT/upf$cpu/rep$k"
    mkdir -p "$base"
    echo "=== UPF $cpu, repetition $k of $REPS ($(date '+%H:%M:%S'))"
    KUBECTL="$KUBECTL" NODES="$NODES" NS="$NS|bench" OUT="$base" TAG=e2 DURATION=900 "$HERE/cadvisor_sample.sh" > "$base/sampler.log" 2>&1 &
    sampler=$!
    KUBECTL="$KUBECTL" NS="$NS" NODESEL="$NODESEL" RUNS=1 DURATION="$DURATION" UDP_RATES="" PATHS="pod pr" PING_COUNT=50 \
      PR_CPU="$PR_CPU" TOOLS_CPU="$TOOLS_CPU" OUT="$base" "$HERE/run_packetrusher.sh" dataplane > "$base/run.log" 2>&1
    rc=$?
    kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
    [ "$rc" = 0 ] || echo "    WARNING: the run ended with exit code $rc (see $base/run.log)"
    summary "$base"
    sleep 10
  done
done
echo "##### all UPF values finished ($(date '+%H:%M:%S')): $OUT"
