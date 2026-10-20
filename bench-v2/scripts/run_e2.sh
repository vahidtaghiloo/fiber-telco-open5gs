#!/usr/bin/env bash
# bench-v2 E2 (minimal): user-plane throughput, REPS repetitions (default 3) of one variant.
# Each repetition is one `run_packetrusher.sh dataplane` (AMF restart, one PacketRusher UE with its GTP tunnel) with two paths
# from the pod's tools container to the iperf3 server (namespace bench, 1 CPU Guaranteed):
#   pod : pod network only, no 5G                    -> the ceiling of the generator and the network (rule F7)
#   pr  : bound to the UE address: tunnel -> UPF -> server  -> the user plane
# Tests per path: TCP with 1 and 4 streams, UDP at UDP_RATES, ping; DURATION s each, one run per repetition.
# The cAdvisor sampler runs during each repetition (Open5GS namespace and bench) for CPU and throttling.
# Results: $OUT/rep<k>/iperf_<path>_*.json, ping_<path>.txt, cadvisor_e2.csv, run.log
#
#   nested (telco-cp):  OUT=~/bench-v2-results/e2/nested ./run_e2.sh
#   direct (server):    KUBECTL="kubectl $WKC" NS=open5gs-direct NODESEL=0 NODES=fiber-telco-open5gs-workload-cp-0 OUT=$HOME/bench-v2-results/e2/direct ./run_e2.sh
set -uo pipefail
OUT=${OUT:?set OUT to the output directory of the variant}
REPS=${REPS:-3}
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
NODESEL=${NODESEL:-1}
NODES=${NODES:-"telco-cp telco-core telco-edge"}
DURATION=${DURATION:-20}
UDP_RATES=${UDP_RATES:-"100M 200M 300M 400M"}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"

summary() {   # $1 = repetition folder: one line per iperf3 file
  python3 - "$1" <<'PY'
import glob, json, os, sys
for f in sorted(glob.glob(os.path.join(sys.argv[1], "iperf_*.json"))):
    name = os.path.basename(f)[6:-5]
    try:
        o = json.load(open(f))
    except Exception:
        print(f"    {name:18s} UNREADABLE"); continue
    if "error" in o:
        print(f"    {name:18s} ERROR: {o['error']}"); continue
    e = o["end"]
    if "_tcp_" in name:
        print(f"    {name:18s} {e['sum_received']['bits_per_second'] / 1e6:7.0f} Mbit/s   retransmits {e['sum_sent'].get('retransmits', 0)}")
    else:
        s = e.get("sum_received", e["sum"])
        print(f"    {name:18s} {s['bits_per_second'] / 1e6:7.0f} Mbit/s   loss {e['sum'].get('lost_percent', 0):.2f} %")
PY
}

for k in $(seq 1 "$REPS"); do
  base="$OUT/rep$k"
  mkdir -p "$base"
  echo "##### repetition $k of $REPS ($(date '+%H:%M:%S'))"
  KUBECTL="$KUBECTL" NODES="$NODES" NS="$NS|bench" OUT="$base" TAG=e2 DURATION=900 "$HERE/cadvisor_sample.sh" > "$base/sampler.log" 2>&1 &
  sampler=$!
  KUBECTL="$KUBECTL" NS="$NS" NODESEL="$NODESEL" RUNS=1 DURATION="$DURATION" UDP_RATES="$UDP_RATES" PATHS="pod pr" PING_COUNT=100 \
    OUT="$base" "$HERE/run_packetrusher.sh" dataplane > "$base/run.log" 2>&1
  rc=$?
  kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
  [ "$rc" = 0 ] || echo "    WARNING: the run ended with exit code $rc (see $base/run.log)"
  summary "$base"
  sleep 10
done
echo "##### all $REPS repetitions finished ($(date '+%H:%M:%S')): $OUT"
