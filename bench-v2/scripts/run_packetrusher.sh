#!/usr/bin/env bash
# bench-v2 copy of bench/run_packetrusher.sh (pilot). Usage comments still name the pilot's folders; pass OUT=~/bench-v2-results/... instead.
# Uses scripts/manifests/packetrusher.yaml, which in bench-v2 has CPU/memory limits (edge budget: 1.5 CPU, 4 GiB in total).
# PacketRusher driver (second UE/gNB generator; see BENCHMARKS.md section 16).
#
#   smoke      one short run with 2 UEs and a tunnel; prints everything needed to validate the setup
#   scaling    control plane: N UEs registering at a controlled pace, REPS times per N;
#              saves each run's JSON report and log:  $OUT/pr_N<nnn>_r<rr>.json / .log
#   dataplane  one UE with a tunnel; iperf3 and ping bound to its address (path name "pr"):
#              $OUT/iperf_pr_<proto>_<tag>.json and $OUT/ping_pr.txt; PATHS="pod pr" adds the same tests from the same
#              container over the pod network only (path "pod", no 5G): $OUT/iperf_pod_*.json, $OUT/ping_pod.txt
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
AMF_DEP=${AMF_DEP:-open5gs-amf}
RESET_AMF=${RESET_AMF:-1}              # 1 = restart the AMF first (drops leftover UE contexts, see BENCHMARKS.md 16.11)
OUT=${OUT:?set OUT to the output directory}
RUNS=${RUNS:-10}
DURATION=${DURATION:-20}
UDP_RATES=${UDP_RATES:-"50M 100M 200M"}
SKIP_TCP=${SKIP_TCP:-0}
PATHS=${PATHS:-pr}                      # dataplane: "pr" = bound to the UE address (tunnel -> UPF -> server); "pod" = same container, pod network only (no 5G)
PING_COUNT=${PING_COUNT:-200}
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

reset_amf() {
  # Open5GS keeps a UE's registration after the UE disappears (UERANSIM's pod is simply removed) and, when the same IMSI
  # registers again, copies only the first bytes of the new security capability over the stored one. A UE left by UERANSIM
  # (which advertises E-UTRA capabilities) then makes the AMF replay a longer capability than PacketRusher offered, and
  # PacketRusher rejects the Security Mode Command. Restarting the AMF clears all such contexts.
  # The UERANSIM UEs must be gone BEFORE the AMF restarts: if they are still running they re-register with the new AMF and
  # leave exactly the stale contexts this restart is meant to remove (seen in the first direct run, N=1).
  $K scale deploy/"$UE_DEP" --replicas=0 >/dev/null 2>&1 || true
  $K wait --for=delete pod -l app.kubernetes.io/component=ues --timeout=90s >/dev/null 2>&1 || true
  [ "$RESET_AMF" = 1 ] || return 0
  echo "-- restarting the AMF ($AMF_DEP) to drop leftover UE contexts (RESET_AMF=0 skips this)"
  $K rollout restart deploy/"$AMF_DEP" >/dev/null && $K rollout status deploy/"$AMF_DEP" --timeout=180s >/dev/null && sleep 10 \
    || echo "WARNING: the AMF restart did not complete; leftover contexts may make the first registrations fail"
}

cleanup() { $K delete pod packetrusher --ignore-not-found --wait=false >/dev/null 2>&1 || true; }

case "$MODE" in
smoke)
  echo "== smoke run: 2 UEs with a tunnel (image $IMAGE, backend $BACKEND)"
  reset_amf
  start_run 2 tunnel
  # CAPTURE=1 records the N2 (SCTP) traffic in the tools container, to look at the NAS messages if something fails
  [ "${CAPTURE:-0}" = 1 ] && $K exec packetrusher -c tools -- sh -c 'nohup tcpdump -U -i any -w /out/n2.pcap sctp >/tmp/tcpdump.log 2>&1 &' >/dev/null 2>&1 || true
  echo "-- waiting 25 s for registration and PDU sessions"; sleep 25
  echo "-- init container (rendered config head):"; $K logs packetrusher -c render-config 2>&1 | head -12
  echo "-- packetrusher log (last 40 lines):"; $K logs packetrusher -c packetrusher 2>&1 | tail -40
  echo "-- interfaces in the pod:"; $K exec packetrusher -c tools -- ip -br addr
  UEIP=$(ue_ip); echo "-- first UE address: ${UEIP:-NOT FOUND}"
  if [ -n "$UEIP" ]; then
    SRV=$($KUBECTL -n bench get pod -l app=iperf-server -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true)
    if [ -n "$SRV" ]; then echo "-- ping $SRV from the UE address:"; $K exec packetrusher -c tools -- ping -c 3 -I "$UEIP" "$SRV" || true; fi
  fi
  stop_run; collect smoke
  [ "${CAPTURE:-0}" = 1 ] && { $K cp -c tools packetrusher:/out/n2.pcap "$OUT/smoke_n2.pcap" >/dev/null 2>&1 && echo "(capture saved: $OUT/smoke_n2.pcap)" || true; }
  echo "-- report.json:"; cat "$OUT/smoke.json" 2>/dev/null || echo "(none)"
  cleanup
  echo "== smoke done; files: $OUT/smoke.log, $OUT/smoke.json"
  ;;
scaling)
  LIST=${2:-"1 10 100"}; REPS=${3:-3}
  reset_amf
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
  echo "finished. UERANSIM UEs were scaled to 0 and the AMF was restarted: to use UERANSIM again, follow BENCHMARKS.md section 12 (including restarting the UERANSIM gNB)."
  ;;
dataplane)
  reset_amf
  start_run 1 tunnel
  echo "-- waiting 25 s for the UE and its tunnel"; sleep 25
  UEIP=$(ue_ip); [ -n "$UEIP" ] || { echo "no UE address found (no val* interface); packetrusher log:"; $K logs packetrusher -c packetrusher 2>&1 | tail -30; cleanup; exit 1; }
  echo "UE address: $UEIP"
  SRV=$($KUBECTL -n bench get pod -l app=iperf-server -o jsonpath='{.items[0].status.podIP}')
  [ -n "$SRV" ] || { echo "iperf-server pod not found (apply manifests/bench-ns-and-servers.yaml)"; cleanup; exit 1; }
  echo "iperf3 server pod IP: $SRV"
  {
    echo "SRV=$SRV; UEIP=$UEIP"
    echo 'run() { name=$1; bind=$2; shift 2; echo "@@@ $name"; i=0'
    echo "  while [ \$i -lt $RUNS ]; do iperf3 -c \$SRV \$bind -t $DURATION -J \"\$@\" || true; i=\$((i+1)); sleep 3; done; }"
    for p in $PATHS; do
      if [ "$p" = pr ]; then bind='-B $UEIP'; pif='-I $UEIP'; else bind=''; pif=''; fi
      [ "$SKIP_TCP" = 1 ] || { echo "run iperf_${p}_tcp_P1.json \"$bind\" -P 1"; echo "run iperf_${p}_tcp_P4.json \"$bind\" -P 4"; }
      echo "for b in $UDP_RATES; do run iperf_${p}_udp_b\$b.json \"$bind\" -u -b \$b; done"
      [ "$SKIP_TCP" = 1 ] || { echo "echo \"@@@ ping_${p}.txt\""; echo "ping -c $PING_COUNT -i 0.2 $pif \$SRV"; }
    done
  } > /tmp/suite_pr.sh
  $K exec -i packetrusher -c tools -- sh -s < /tmp/suite_pr.sh | awk -v out="$OUT" '/^@@@ /{f=$2; next} f{print > (out "/" f)}'
  stop_run; collect pr_dataplane_run
  cleanup
  ls -l "$OUT"
  bad=0
  expected=$(( $(echo $UDP_RATES | wc -w) + (SKIP_TCP == 1 ? 0 : 2) ))
  for p in $PATHS; do
    n=$(ls "$OUT"/iperf_${p}_*.json 2>/dev/null | wc -l)
    [ "$n" -ge "$expected" ] || { echo "WARNING: only $n iperf_${p} files (expected $expected)"; bad=1; }
    [ "$SKIP_TCP" = 1 ] || [ -s "$OUT/ping_${p}.txt" ] || { echo "WARNING: no ping_${p}.txt"; bad=1; }
  done
  [ "$bad" = 0 ] && echo "OK: the PacketRusher data-plane files are present" || { echo "INCOMPLETE: see the warnings above"; exit 1; }
  ;;
*) echo "unknown mode '$MODE' (smoke | scaling | dataplane)"; exit 2 ;;
esac
