#!/usr/bin/env bash
# PacketRusher driver (second UE/gNB generator; see BENCHMARKS.md section 16).
#
#   smoke      one short run with 2 UEs and a tunnel; prints everything needed to validate the setup
#   scaling    control plane: N UEs registering at a controlled pace, REPS times per N;
#              saves each run's JSON report and log:  $OUT/pr_N<nnn>_r<rr>.json / .log
#   dataplane  one UE with a tunnel; iperf3 and ping bound to its address (path name "pr"):
#              $OUT/iperf_pr_<proto>_<tag>.json and $OUT/ping_pr.txt
#
# usage (telco-cp):
#   OUT=~/bench-results/nested/pr ./run_packetrusher.sh smoke
#   OUT=~/bench-results/nested/pr ./run_packetrusher.sh scaling "1 10 100 500" 3
#   OUT=~/bench-results/nested/dp RUNS=10 DURATION=20 ./run_packetrusher.sh dataplane
# baseline (server):  KUBECTL="kubectl $WKC" NS=open5gs-direct NODESEL=0 UE_DEP=... (same modes)
#
# Prerequisites: manifests/bench-ns-and-servers.yaml applied (dataplane mode), subscribers provisioned for max(N)
# (provision_subscribers.sh), and the ueransim HelmRelease suspended: this script scales the UERANSIM UE deployment
# to 0 because both generators use the same IMSIs.
set -euo pipefail
MODE=${1:?mode: smoke | scaling | dataplane}
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
IMAGE=${IMAGE:-ghcr.io/hewlettpackard/packetrusher:v2.0.0}
BACKEND=${BACKEND:-userspace}          # ebpf needs Linux >= 6.6; the telco VMs run 5.15
TR=${TR:-10}                            # ms between UE registrations (PacketRusher's own default is 500)
SLACK=${SLACK:-30}                      # seconds to let a run settle after the last registration
NEA2=${NEA2:-false}                     # advertise NEA2 as well (upstream's Open5GS CI advertises nia2 + nea0 only)
NODESEL=${NODESEL:-1}                   # 1 = pin the pod to telco-edge; 0 = no nodeSelector (direct variant)
AMF_SVC=${AMF_SVC:-open5gs-amf-ngap}
UE_DEP=${UE_DEP:-ueransim-ueransim-gnb-ues}
OUT=${OUT:?set OUT to the output directory}
RUNS=${RUNS:-10}
DURATION=${DURATION:-20}
UDP_RATES=${UDP_RATES:-"50M 100M 200M"}
SKIP_TCP=${SKIP_TCP:-0}
TPL="$(cd "$(dirname "$0")" && pwd)/manifests/packetrusher.yaml"
mkdir -p "$OUT"
K="$KUBECTL -n $NS"

render() {   # $1 = N, $2 = "tunnel" or ""
  local amf; amf=$($K get svc "$AMF_SVC" -o jsonpath='{.spec.clusterIP}')
  [ -n "$amf" ] || { echo "service $AMF_SVC not found in $NS" >&2; exit 1; }
  local tunnel_expr nodesel_expr
  if [ "${2:-}" = tunnel ]; then tunnel_expr="s#@TUNNEL_LINE@#--tunnel#"; else tunnel_expr="/@TUNNEL_LINE@/d"; fi
  if [ "$NODESEL" = 1 ]; then nodesel_expr="s#@NODESEL@#nodeSelector: {telco.example/site: edge}#"; else nodesel_expr="/@NODESEL@/d"; fi
  sed -e "s#@NS@#$NS#g" -e "s#@IMAGE@#$IMAGE#g" -e "s#@BACKEND@#$BACKEND#g" -e "s#@N@#$1#g" -e "s#@TR@#$TR#g" \
      -e "s#@AMF_IP@#$amf#g" -e "s#@NEA2@#$NEA2#g" -e "$tunnel_expr" -e "$nodesel_expr" "$TPL"
}

state() {    # $1 = container, $2 = running|terminated  -> prints the state's JSON (empty if not in that state)
  $K get pod packetrusher -o jsonpath="{.status.containerStatuses[?(@.name==\"$1\")].state.$2}" 2>/dev/null || true
}

start_run() {  # $1 = N, $2 = "tunnel" or ""
  $K delete pod packetrusher --ignore-not-found --wait=true >/dev/null 2>&1 || true
  $K scale deploy/"$UE_DEP" --replicas=0 >/dev/null 2>&1 || true
  render "$1" "${2:-}" | $K apply -f - >/dev/null
  local i
  for i in $(seq 1 90); do
    [ -n "$(state packetrusher running)" ] && [ -n "$(state tools running)" ] && return 0
    if [ -n "$(state packetrusher terminated)" ]; then
      echo "packetrusher exited early; its log:"; $K logs packetrusher -c packetrusher 2>&1 | tail -30; return 1
    fi
    sleep 2
  done
  echo "pod did not start in time:"; $K describe pod packetrusher | tail -25; return 1
}

stop_run() {   # SIGINT makes PacketRusher write its report; waits for it to exit (and says what happened if it does not)
  local out i
  out=$($K exec packetrusher -c tools -- sh -c 'p=$(pidof packetrusher); echo "pid(s) of packetrusher: [$p]"; kill -INT $p; echo "kill -INT exit status: $?"' 2>&1) || true
  echo "   stop: $out" | tr '\n' ' '; echo
  for i in $(seq 1 15); do
    [ -n "$(state packetrusher terminated)" ] && return 0
    sleep 2
  done
  echo "WARNING: packetrusher did not exit within 30 s after SIGINT. Processes in the pod:"
  $K exec packetrusher -c tools -- ps 2>&1 | head -15 || true
  echo "last lines of its log:"; $K logs packetrusher -c packetrusher --tail=5 2>&1 || true
  $K exec packetrusher -c tools -- sh -c 'kill -TERM $(pidof packetrusher)' >/dev/null 2>&1 || true
  for i in $(seq 1 10); do
    [ -n "$(state packetrusher terminated)" ] && { echo "(it exited after SIGTERM)"; return 0; }
    sleep 2
  done
  echo "WARNING: still running after SIGTERM; the pod will be deleted without a report"
}

collect() {    # $1 = file stem
  $K logs packetrusher -c packetrusher > "$OUT/$1.log" 2>&1 || true
  $K exec packetrusher -c tools -- cat /out/report.json > "$OUT/$1.json" 2>/dev/null || { echo "no report.json for $1"; rm -f "$OUT/$1.json"; }
}

ue_ip() {      # address of the first UE's val* interface
  $K exec packetrusher -c tools -- sh -c "ip -4 -o addr show | awk '\$2 ~ /^val/ {split(\$4,a,\"/\"); print a[1]; exit}'" 2>/dev/null || true
}

cleanup() { $K delete pod packetrusher --ignore-not-found --wait=false >/dev/null 2>&1 || true; }

case "$MODE" in
smoke)
  echo "== smoke run: 2 UEs with a tunnel (image $IMAGE, backend $BACKEND)"
  start_run 2 tunnel
  # capture the N2 (SCTP) traffic in the tools container, to be able to look at the NAS messages if something fails
  $K exec packetrusher -c tools -- sh -c 'nohup tcpdump -U -i any -w /out/n2.pcap sctp >/tmp/tcpdump.log 2>&1 &' >/dev/null 2>&1 || true
  echo "-- waiting 25 s for registration and PDU sessions"; sleep 25
  echo "-- init container (rendered config head):"; $K logs packetrusher -c render-config 2>&1 | head -12
  echo "-- packetrusher log (last 40 lines):"; $K logs packetrusher -c packetrusher 2>&1 | tail -40
  echo "-- interfaces in the pod:"; $K exec packetrusher -c tools -- ip -br addr
  UEIP=$(ue_ip); echo "-- first UE address: ${UEIP:-NOT FOUND}"
  if [ -n "$UEIP" ]; then
    SRV=$($KUBECTL -n bench get pod -l app=iperf-server -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true)
    if [ -n "$SRV" ]; then echo "-- ping $SRV from the UE address:"; $K exec packetrusher -c tools -- ping -c 3 -I "$UEIP" "$SRV" || true; fi
  fi
  echo "-- NAS security capability, from the capture (Registration Request, Security Mode Command):"
  $K exec packetrusher -c tools -- sh -c 'if command -v tshark >/dev/null; then tshark -r /out/n2.pcap -Y "nas-5gs" -V 2>/dev/null | grep -i -E "Message type|security capab|5G-EA|5G-IA|EEA|EIA|Selected NAS|replayed" | head -80; else echo "(no tshark; raw packets with a NAS payload follow)"; tcpdump -nn -r /out/n2.pcap -x 2>/dev/null | head -150; fi' 2>&1 || true
  stop_run; collect smoke
  $K cp -c tools packetrusher:/out/n2.pcap "$OUT/smoke_n2.pcap" >/dev/null 2>&1 && echo "(capture saved: $OUT/smoke_n2.pcap)" || true
  echo "-- report.json:"; cat "$OUT/smoke.json" 2>/dev/null || echo "(none)"
  cleanup
  echo "== smoke done; files: $OUT/smoke.log, $OUT/smoke.json"
  ;;
scaling)
  LIST=${2:-"1 10 100"}; REPS=${3:-3}
  for n in $LIST; do
    for r in $(seq 1 "$REPS"); do
      tag=$(printf 'pr_N%03d_r%02d' "$n" "$r")
      echo "== N=$n run $r/$REPS (registration gap ${TR} ms)"
      start_run "$n" ""
      wait_s=$(( n * TR / 1000 + SLACK )); echo "   running for ${wait_s} s"; sleep "$wait_s"
      stop_run; collect "$tag"
      echo "   -> $OUT/$tag.json  ($(grep -c . "$OUT/$tag.log" 2>/dev/null || echo 0) log lines)"
      cleanup; sleep 5
    done
  done
  echo "finished. UERANSIM UEs were scaled to 0: restore them as described in BENCHMARKS.md section 12."
  ;;
dataplane)
  start_run 1 tunnel
  echo "-- waiting 25 s for the UE and its tunnel"; sleep 25
  UEIP=$(ue_ip); [ -n "$UEIP" ] || { echo "no UE address found (no val* interface); packetrusher log:"; $K logs packetrusher -c packetrusher 2>&1 | tail -30; cleanup; exit 1; }
  echo "UE address: $UEIP"
  SRV=$($KUBECTL -n bench get pod -l app=iperf-server -o jsonpath='{.items[0].status.podIP}')
  [ -n "$SRV" ] || { echo "iperf-server pod not found (apply manifests/bench-ns-and-servers.yaml)"; cleanup; exit 1; }
  echo "iperf3 server pod IP: $SRV"
  {
    echo "SRV=$SRV; UEIP=$UEIP"
    echo 'run() { name=$1; shift; echo "@@@ $name"; i=0'
    echo "  while [ \$i -lt $RUNS ]; do iperf3 -c \$SRV -B \$UEIP -t $DURATION -J \"\$@\" || true; i=\$((i+1)); sleep 3; done; }"
    [ "$SKIP_TCP" = 1 ] || { echo 'run iperf_pr_tcp_P1.json -P 1'; echo 'run iperf_pr_tcp_P4.json -P 4'; }
    echo "for b in $UDP_RATES; do run iperf_pr_udp_b\$b.json -u -b \$b; done"
    [ "$SKIP_TCP" = 1 ] || { echo 'echo "@@@ ping_pr.txt"'; echo 'ping -c 200 -i 0.2 -I $UEIP $SRV'; }
  } > /tmp/suite_pr.sh
  $K exec -i packetrusher -c tools -- sh -s < /tmp/suite_pr.sh | awk -v out="$OUT" '/^@@@ /{f=$2; next} f{print > (out "/" f)}'
  stop_run; collect pr_dataplane_run
  cleanup
  ls -l "$OUT"
  bad=0
  n=$(ls "$OUT"/iperf_pr_*.json 2>/dev/null | wc -l)
  [ "$SKIP_TCP" = 1 ] || { [ "$n" -ge 5 ] || { echo "WARNING: only $n iperf_pr files (expected 5)"; bad=1; }; [ -s "$OUT/ping_pr.txt" ] || { echo "WARNING: no ping_pr.txt"; bad=1; }; }
  [ "$bad" = 0 ] && echo "OK: the PacketRusher data-plane files are present" || { echo "INCOMPLETE: see the warnings above"; exit 1; }
  ;;
*) echo "unknown mode '$MODE' (smoke | scaling | dataplane)"; exit 2 ;;
esac
