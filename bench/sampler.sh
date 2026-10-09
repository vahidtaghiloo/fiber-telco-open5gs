#!/usr/bin/env bash
# Samples `kubectl top` into a CSV: ts,kind,ns,name,cpu_m,mem_mi   (kind = pod|node)
# Needs metrics-server in the cluster you sample.
#
# telco level (inside telco-cp):
#   SCOPE=telco PHASE=idle DURATION=60 OUT=~/bench-results/nested/res ./sampler.sh
# fiber-company level (server): what the three VMs cost the workload cluster
#   KUBECTL="kubectl $WKC" PODS_NS=telco SCOPE=fiber PHASE=idle DURATION=60 OUT=$HOME/bench-results/nested/res ./sampler.sh
set -euo pipefail
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
SCOPE=${SCOPE:?telco|fiber}
PHASE=${PHASE:?idle|ue|iperf}
OUT=${OUT:?output dir}
DURATION=${DURATION:-60}
INTERVAL=${INTERVAL:-5}
PODS_NS=${PODS_NS:-}
mkdir -p "$OUT"
F="$OUT/res_${SCOPE}_${PHASE}.csv"
echo "ts,kind,ns,name,cpu_m,mem_mi" > "$F"

conv='
function cpu(x){ if (x ~ /m$/) { sub(/m$/,"",x); return x } if (x ~ /n$/) { sub(/n$/,"",x); return x/1000000 } return x*1000 }
function mem(x){ if (x ~ /Ki$/) { sub(/Ki$/,"",x); return x/1024 } if (x ~ /Gi$/) { sub(/Gi$/,"",x); return x*1024 } sub(/Mi$/,"",x); return x }'

end=$((SECONDS + DURATION))
while [ $SECONDS -lt $end ]; do
  ts=$(date +%s)
  if [ -n "$PODS_NS" ]; then
    $KUBECTL top pod -n "$PODS_NS" --no-headers 2>/dev/null | awk -v ts=$ts -v ns="$PODS_NS" "$conv"' {print ts",pod,"ns","$1","cpu($2)","mem($3)}' >> "$F" || true
  else
    $KUBECTL top pod -A --no-headers 2>/dev/null | awk -v ts=$ts "$conv"' {print ts",pod,"$1","$2","cpu($3)","mem($4)}' >> "$F" || true
  fi
  $KUBECTL top node --no-headers 2>/dev/null | awk -v ts=$ts "$conv"' {print ts",node,,"$1","cpu($2)","mem($4)}' >> "$F" || true
  sleep "$INTERVAL"
done
echo "wrote $F ($(wc -l < "$F") lines)"
