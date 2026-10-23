#!/usr/bin/env bash
# bench-v2 E6: how long the telco's Open5GS takes from "apply" to the first registered phone, per variant, REPS times.
# Non-destructive: a scratch copy of Open5GS (same chart, values and FIXED limits) goes into its own namespace NS_E6, is timed,
# and is deleted again; the main deployment and the telco VMs are not touched. The main Open5GS of BOTH variants must be scaled
# to 0 first (RUNBOOK step 39), so that the copy has the same room as the main deployment.
# The VM layer of nested (disk import, VM boot, k3s, Calico) is not re-created here: re-creating the VMs is destructive (Flux
# re-creates deleted VMs by itself, and a restarted VM loses its first network card, REBUILD.md); its times are read from the
# existing objects instead (RUNBOOK step 38).
# Timeline per repetition (seconds from the apply): all pods Ready, first successful PacketRusher smoke test (2 phones
# registered), namespace deleted. Images are already cached on the nodes, so these are warm starts.
# Results: $OUT/rep<k>/timeline.txt, pods.txt, smoke_try<i>/smoke.json
#
#   nested (telco-cp):  OUT=~/bench-v2-results/e6/nested ./run_e6.sh
#   direct (server):    KUBECTL="kubectl $WKC" VALUES=values/open5gs-direct.yaml NODESEL=0 OUT=$HOME/bench-v2-results/e6/direct ./run_e6.sh
set -uo pipefail
OUT=${OUT:?set OUT to the output directory of the variant}
REPS=${REPS:-3}
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS_E6=${NS_E6:-open5gs-e6}
VALUES=${VALUES:-values/open5gs-nested.yaml}
NODESEL=${NODESEL:-1}
TRIES=${TRIES:-8}
HERE=$(cd "$(dirname "$0")" && pwd)
V2=$(cd "$HERE/.." && pwd)
K="$KUBECTL -n $NS_E6"
mkdir -p "$OUT"

if $KUBECTL get namespace "$NS_E6" >/dev/null 2>&1; then echo "namespace $NS_E6 exists already; delete it first ($KUBECTL delete namespace $NS_E6)"; exit 1; fi
echo "--- rendering once (not timed)"
helm template open5gs oci://registry-1.docker.io/gradiant/open5gs --version 2.2.6 --namespace "$NS_E6" \
  -f "$V2/$VALUES" -f "$V2/values/resources-fixed.yaml" -f "$V2/values/update-recreate.yaml" > "$OUT/manifest.yaml" || { echo "helm template failed"; exit 1; }
EXPECTED=$(grep -c '^kind: Deployment' "$OUT/manifest.yaml")
echo "    $EXPECTED deployments in the manifest"

ready_pods() { $K get pods --no-headers 2>/dev/null | awk '{ split($2, a, "/"); if (a[1] == a[2] && $3 == "Running") n++ } END { print n + 0 }'; }
since() { awk -v a="$1" -v b="$(date +%s.%N)" 'BEGIN { printf "%.1f", b - a }'; }

for k in $(seq 1 "$REPS"); do
  base="$OUT/rep$k"
  mkdir -p "$base"
  echo "##### repetition $k of $REPS ($(date '+%H:%M:%S'))"
  t0=$(date +%s.%N)
  $KUBECTL create namespace "$NS_E6" >/dev/null
  $KUBECTL label namespace "$NS_E6" pod-security.kubernetes.io/enforce=privileged >/dev/null
  $K apply -f "$OUT/manifest.yaml" >/dev/null
  t_apply=$(since "$t0")
  t_ready=""
  for _ in $(seq 1 450); do
    [ "$(ready_pods)" -ge "$EXPECTED" ] && { t_ready=$(since "$t0"); break; }
    sleep 2
  done
  $K get pods -o wide > "$base/pods.txt" 2>&1
  echo "    applied after ${t_apply} s; all $EXPECTED pods Ready after ${t_ready:-NEVER (900 s)} s"
  t_reg=""
  if [ -n "$t_ready" ]; then
    for i in $(seq 1 "$TRIES"); do
      KUBECTL="$KUBECTL" NS="$NS_E6" NODESEL="$NODESEL" RESET_AMF=0 OUT="$base/smoke_try$i" "$HERE/run_packetrusher.sh" smoke > "$base/smoke_try$i.log" 2>&1
      ok=$(python3 -c 'import json,sys; p={x["procedure"]: x for x in json.load(open(sys.argv[1]))["procedures"]}; print(p["registration"]["success"])' "$base/smoke_try$i/smoke.json" 2>/dev/null || echo 0)
      echo "    smoke try $i: $ok of 2 phones registered ($(since "$t0") s)"
      if [ "$ok" = 2 ]; then   # the moment of the first "Receive RegAccept" in PacketRusher's log (UTC), relative to the apply
        t_reg=$(python3 -c '
import datetime, sys
for l in open(sys.argv[1]):
    if "Receive RegAccept" in l:
        t = datetime.datetime.strptime(l.split()[0], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc).timestamp()
        print(f"{t - float(sys.argv[2]):.1f}"); break' "$base/smoke_try$i/smoke.log" "$t0" 2>/dev/null)
        [ -n "$t_reg" ] || t_reg=$(since "$t0")
        break
      fi
      sleep 10
    done
  fi
  t_del0=$(date +%s.%N)
  $KUBECTL delete namespace "$NS_E6" --timeout=600s >/dev/null 2>&1 || echo "    WARNING: namespace $NS_E6 not deleted within 600 s"
  t_del=$(since "$t_del0")
  printf 'apply_s %s\nall_pods_ready_s %s\nfirst_registration_s %s\nnamespace_delete_s %s\n' "$t_apply" "${t_ready:-NA}" "${t_reg:-NA}" "$t_del" > "$base/timeline.txt"
  echo "    first registration after ${t_reg:-NEVER} s; namespace deleted in ${t_del} s"
  sleep 30
done
echo "##### all $REPS repetitions finished ($(date '+%H:%M:%S')): $OUT"
