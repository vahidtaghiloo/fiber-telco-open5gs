#!/usr/bin/env bash
# bench-v2 E3: which control-plane function limits capacity first. One CPU limit at a time is LOWERED (requests = limits,
# memory unchanged): the core VM of nested has only 0.5 vCPU spare (README section 4), so limits cannot be raised fairly.
# Load: the E1 capacity point, 1000 phones at 50 per second (PacketRusher, TR=20 ms), REPS runs per setting, with the cAdvisor
# sampler (CPU and throttling per container). Before each setting all four functions are set to their FIXED value, the one
# under test is lowered, and all network functions are restarted (as in E1). The FIXED values are restored at the end.
# Results: $OUT/<setting>/rep<k>/pr_N1000_r01.json and .log, cadvisor_e3.csv
#
#   nested (telco-cp):  OUT=~/bench-v2-results/e3/nested ./run_e3.sh
#   direct (server):    KUBECTL="kubectl $WKC" NS=open5gs-direct NODESEL=0 NODES=fiber-telco-open5gs-workload-cp-0 OUT=$HOME/bench-v2-results/e3/direct ./run_e3.sh
set -uo pipefail
OUT=${OUT:?set OUT to the output directory of the variant}
REPS=${REPS:-3}
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
NODESEL=${NODESEL:-1}
NODES=${NODES:-"telco-core telco-edge"}
SETTINGS=${SETTINGS:-"base amf=250m amf=500m amf=750m smf=250m smf=500m smf=750m mongodb=500m mongodb=1000m udm=250m"}
TR=${TR:-20}
N=${N:-1000}
HERE=$(cd "$(dirname "$0")" && pwd)
K="$KUBECTL -n $NS"
NFS="amf ausf bsf nrf nssf pcf scp smf udm udr upf"
mkdir -p "$OUT"

# FIXED profile (values/resources-fixed.yaml) of the functions that are varied: "CPU memory container"
fixed() {
  case "$1" in
    amf)     echo "1 1Gi open5gs-amf" ;;
    smf)     echo "1 1Gi open5gs-smf" ;;
    mongodb) echo "1500m 3Gi mongodb" ;;
    udm)     echo "500m 512Mi open5gs-udm" ;;
    *)       echo "unknown function '$1'" >&2; return 1 ;;
  esac
}
ctr() { fixed "$1" | cut -d' ' -f3; }

set_cpu() {   # $1 = function, $2 = CPU (memory and container from the FIXED profile)
  local nf=$1 cpu=$2 spec mem c
  spec=$(fixed "$nf") || return 1
  mem=$(echo "$spec" | cut -d' ' -f2); c=$(echo "$spec" | cut -d' ' -f3)
  $K set resources deploy/open5gs-"$nf" -c "$c" --requests="cpu=$cpu,memory=$mem" --limits="cpu=$cpu,memory=$mem" >/dev/null
}
all_base() { for f in amf smf mongodb udm; do set_cpu "$f" "$(fixed "$f" | cut -d' ' -f1)"; done; }
restart_core() {
  $K rollout status deploy/open5gs-mongodb --timeout=600s >/dev/null || echo "    WARNING: open5gs-mongodb not ready after 600 s"
  for d in $NFS; do $K rollout restart deploy/open5gs-$d >/dev/null 2>&1; done
  for d in $NFS; do $K rollout status deploy/open5gs-$d --timeout=300s >/dev/null 2>&1 || echo "    WARNING: open5gs-$d not ready after 300 s"; done
  sleep 30
}
limits() { for f in amf smf mongodb udm; do printf '%s=%s ' "$f" "$($K get deploy open5gs-$f -o jsonpath="{.spec.template.spec.containers[?(@.name==\"$(ctr $f)\")].resources.limits.cpu}")"; done; }

restore() { echo "--- restoring the FIXED limits"; all_base; restart_core; echo "    limits now: $(limits)"; }
trap restore EXIT

for s in $SETTINGS; do
  echo "##### setting $s ($(date '+%H:%M:%S'))"
  all_base
  if [ "$s" != base ]; then set_cpu "${s%%=*}" "${s#*=}"; fi
  restart_core
  echo "    limits now: $(limits)"
  name=${s/=/-}
  for k in $(seq 1 "$REPS"); do
    base="$OUT/$name/rep$k"
    mkdir -p "$base"
    KUBECTL="$KUBECTL" NODES="$NODES" NS="$NS" OUT="$base" TAG=e3 DURATION=600 "$HERE/cadvisor_sample.sh" > "$base/sampler.log" 2>&1 &
    sampler=$!
    TR=$TR KUBECTL="$KUBECTL" NS="$NS" NODESEL="$NODESEL" OUT="$base" "$HERE/run_packetrusher.sh" scaling "$N" 1 > "$base/run.log" 2>&1
    kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
    f=$(ls "$base"/pr_N*_r01.json 2>/dev/null | head -1)
    if [ -n "$f" ]; then
      python3 - "$f" "$N" "$k" <<'PY'
import json, sys
p = {x["procedure"]: x for x in json.load(open(sys.argv[1]))["procedures"]}
r = p["registration"]
print(f"    rep {sys.argv[3]}: registered {r['success']} of {sys.argv[2]}, rejected {r['failure']}, pending {r['pending']}, "
      f"mean registration {r['latency_seconds_mean'] * 1000:.0f} ms")
PY
    else
      echo "    rep $k: WARNING: no report (see $base/run.log)"
    fi
  done
done
echo "##### all settings finished ($(date '+%H:%M:%S')): $OUT"
