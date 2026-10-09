#!/usr/bin/env bash
# Adds subscribers 3..MAX to Open5GS MongoDB (1 and 2 already come from the populate job).
# IMSI = 99970 + 10-digit counter, same key/OPc as the existing subscribers, so UERANSIM
# can register up to MAX UEs (it counts up from imsi-999700000000001).
#
# usage (inside telco-cp):   ./provision_subscribers.sh 200
# usage (baseline, server):  KUBECTL="kubectl $WKC" NS=open5gs-direct ./provision_subscribers.sh 200
set -euo pipefail
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
NS=${NS:-open5gs}
MAX=${1:-100}
KEY=465B5CE8B199B49FAA5F0A2EE238A6BC
OPC=E8ED289DEBA952E4283B54E88E6183CA

POD=$($KUBECTL -n "$NS" get pods -o name | grep populate | head -1)
[ -n "$POD" ] || { echo "no populate pod in namespace $NS"; exit 1; }
echo "provisioning subscribers 3..$MAX through $POD (can take a few minutes)"
$KUBECTL -n "$NS" exec "$POD" -- sh -c "
i=3
while [ \$i -le $MAX ]; do
  imsi=\$(printf '99970%010d' \$i)
  open5gs-dbctl add_ue_with_slice \$imsi $KEY $OPC internet 1 111111 >/dev/null 2>&1 || echo \"failed: \$imsi\"
  i=\$((i+1))
done
echo done"
