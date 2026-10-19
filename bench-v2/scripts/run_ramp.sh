#!/usr/bin/env bash
# bench-v2 E1: control-plane capacity ramp. PacketRusher registers phones at rising rates, one run per rate.
# For each TR (milliseconds between two phones, offered rate = 1000/TR per second) it registers N = DURATION_S*1000/TR phones,
# so every step offers load for about DURATION_S seconds. Each step is one call of run_packetrusher.sh (which restarts the AMF first).
# Results: $OUT/tr<TR>/pr_N<N>_r01.json and .log
#
#   nested (telco-cp):  OUT=~/bench-v2-results/e1/nested/rep1 ./run_ramp.sh
#   direct (server):    KUBECTL="kubectl $WKC" NS=open5gs-direct NODESEL=0 OUT=$HOME/bench-v2-results/e1/direct/rep1 ./run_ramp.sh
# KUBECTL, NS and NODESEL are passed on to run_packetrusher.sh unchanged.
set -uo pipefail
OUT=${OUT:?set OUT to the output directory}
TRS=${TRS:-"200 100 50 25 12 6"}        # 5, 10, 20, 40, 83, 167 phones per second
DURATION_S=${DURATION_S:-20}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
for tr in $TRS; do
  n=$(( DURATION_S * 1000 / tr ))
  d="$OUT/tr$(printf %03d "$tr")"
  echo "=== offered rate $(awk -v t="$tr" 'BEGIN { printf "%.0f", 1000 / t }') phones/s (TR=$tr ms): $n phones"
  TR=$tr OUT="$d" "$HERE/run_packetrusher.sh" scaling "$n" 1
  f=$(ls "$d"/pr_N*_r01.json 2>/dev/null | head -1)
  if [ -n "$f" ]; then
    grep -A5 '"registration"' "$f" | awk -F: -v n="$n" '/"success"|"failure"|"pending"/ { gsub(/[ ",]/, "", $1); gsub(/[ ,]/, "", $2); printf "%s=%s ", $1, $2 } END { print "of " n }' | sed 's/^/    registration: /'
  else
    echo "    WARNING: no report for TR=$tr"
  fi
done
echo "ramp finished: $OUT"
