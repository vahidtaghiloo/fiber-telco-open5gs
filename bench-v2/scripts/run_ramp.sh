#!/usr/bin/env bash
# bench-v2 E1: control-plane capacity ramp. PacketRusher registers phones at rising rates, one run per rate,
# and repeats the whole ramp REPS times by itself (default 3). Before every repetition all network functions are restarted,
# so no repetition inherits damage from the previous one (an overloaded direct core keeps rejecting phones afterwards).
#
# For each TR (milliseconds between two phones, offered rate = 1000/TR per second) it registers N = DURATION_S*1000/TR phones,
# but at most MAX_N: the Open5GS AMF holds at most 1024 phones at once ("Could not allocate ran_ue context from pool"),
# so a step with more phones measures that ceiling instead of the processing rate. Steps above MAX_N/DURATION_S per second
# therefore last less than DURATION_S seconds. Each step is one call of run_packetrusher.sh (which restarts the AMF first).
#
# Results: $OUT/rep<k>/tr<TR>/pr_N<N>_r01.json and .log, and $OUT/rep<k>/heartbeats.txt
#
#   nested (telco-cp):  OUT=~/bench-v2-results/e1b/nested ./run_ramp.sh
#   direct (server):    KUBECTL="kubectl $WKC" NS=open5gs-direct NODESEL=0 OUT=$HOME/bench-v2-results/e1b/direct ./run_ramp.sh
# KUBECTL, NS and NODESEL are passed on to run_packetrusher.sh unchanged.
set -uo pipefail
OUT=${OUT:?set OUT to the output directory of the variant}
REPS=${REPS:-3}
TRS=${TRS:-"200 100 50 25 20 16 12 6"}  # 5, 10, 20, 40, 50, 62.5, 83, 167 phones per second
DURATION_S=${DURATION_S:-20}
MAX_N=${MAX_N:-1000}                    # below the AMF's 1024-phone pool
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
NFS="amf ausf bsf nrf nssf pcf scp smf udm udr upf"   # populate and MongoDB are not restarted (subscribers stay)
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"

restart_core() {
  echo "--- restarting all network functions in $NS"
  for d in $NFS; do $KUBECTL -n "$NS" rollout restart deploy/open5gs-$d >/dev/null 2>&1; done
  for d in $NFS; do $KUBECTL -n "$NS" rollout status deploy/open5gs-$d --timeout=300s >/dev/null 2>&1 || echo "WARNING: open5gs-$d not ready after 300 s"; done
  sleep 30
}

for k in $(seq 1 "$REPS"); do
  base="$OUT/rep$k"
  mkdir -p "$base"
  echo "##### repetition $k of $REPS ($(date '+%H:%M:%S'))"
  restart_core
  for tr in $TRS; do
    n=$(( DURATION_S * 1000 / tr )); [ "$n" -gt "$MAX_N" ] && n=$MAX_N
    d="$base/tr$(printf %03d "$tr")"
    echo "=== rep $k: offered rate $(awk -v t="$tr" 'BEGIN { printf "%.1f", 1000 / t }') phones/s (TR=$tr ms): $n phones over $(awk -v n="$n" -v t="$tr" 'BEGIN { printf "%.1f", n * t / 1000 }') s"
    TR=$tr OUT="$d" KUBECTL="$KUBECTL" NS="$NS" "$HERE/run_packetrusher.sh" scaling "$n" 1 > "$d.run.log" 2>&1
    f=$(ls "$d"/pr_N*_r01.json 2>/dev/null | head -1)
    if [ -n "$f" ]; then
      grep -A5 '"registration"' "$f" | awk -F: -v n="$n" '/"success"|"failure"|"pending"/ { gsub(/[ ",]/, "", $1); gsub(/[ ,]/, "", $2); printf "%s=%s ", $1, $2 } END { print "of " n }' | sed 's/^/    registration: /'
    else
      echo "    WARNING: no report for TR=$tr (see $d.run.log)"
    fi
  done
  { for d in udr ausf udm smf pcf; do printf "%-5s %s\n" "$d" "$($KUBECTL -n "$NS" logs deploy/open5gs-$d 2>/dev/null | grep -c 'No heartbeat')"; done; } > "$base/heartbeats.txt"
  echo "    lost NF registrations during this repetition: $(awk '{ s += $2 } END { print s + 0 }' "$base/heartbeats.txt")  (details: $base/heartbeats.txt)"
done
echo "##### all $REPS repetitions finished ($(date '+%H:%M:%S')): $OUT"
