#!/usr/bin/env bash
# bench-v2 pre-flight: records the facts that decide whether the two variants are comparable (README section 5).
# Read-only. Writes OUT/preflight_<where>.txt and prints it.
#   on the server (fiber-company level):   KUBECTL="kubectl $WKC" OUT=$HOME/bench-v2-results/env ./preflight.sh server
#   on telco-cp   (telco level, nested):   OUT=$HOME/bench-v2-results/env ./preflight.sh nested
set -uo pipefail
WHERE=${1:?server | nested}
OUT=${OUT:?set OUT to the output directory}
KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
mkdir -p "$OUT"
F="$OUT/preflight_${WHERE}.txt"
sec() { printf '\n===== %s =====\n' "$1"; }
{
  echo "preflight $WHERE  $(date -u +%FT%TZ)  host $(hostname)"
  sec "this machine: CPUs, memory, hypervisor flag, kernel"
  nproc; free -g | head -2; grep -c hypervisor /proc/cpuinfo | sed 's/^/cpuinfo lines with hypervisor flag: /'; uname -r
  sec "nodes"; $KUBECTL get nodes -o wide
  sec "node capacity / allocatable / already requested"
  $KUBECTL describe nodes | grep -E "^Name:|^  cpu|^  memory|Allocated resources|Resource +Requests|^  (cpu|memory) +[0-9]" 
  sec "CNI: kube-system daemonsets"; $KUBECTL -n kube-system get ds -o wide 2>&1 | head -20
  sec "Calico IP pools (encapsulation)"
  $KUBECTL get ippools.crd.projectcalico.org -o jsonpath='{range .items[*]}{.metadata.name}{"  cidr="}{.spec.cidr}{"  ipip="}{.spec.ipipMode}{"  vxlan="}{.spec.vxlanMode}{"  natOutgoing="}{.spec.natOutgoing}{"\n"}{end}' 2>&1
  sec "Calico Installation (operator)"; $KUBECTL get installation default -o yaml 2>&1 | sed -n '/^spec:/,/^status:/p' | head -40
  if [ "$WHERE" = server ]; then
    sec "KubeVirt: version and CPU/memory overcommit settings (cpuAllocationRatio default is 10 = a vCPU requests only 0.1 CPU)"
    $KUBECTL get kubevirt -A -o yaml 2>&1 | grep -iE "cpuAllocationRatio|memoryOvercommit|observedKubeVirtVersion|dedicatedCpu|developerConfiguration" 
    sec "VMs of the telco: vCPU, memory, pod requests/limits of the virt-launcher pods"
    $KUBECTL -n telco get vmi -o wide 2>&1
    $KUBECTL -n telco get pods -l kubevirt.io=virt-launcher -o custom-columns='POD:.metadata.name,CPU_REQ:.spec.containers[?(@.name=="compute")].resources.requests.cpu,CPU_LIM:.spec.containers[?(@.name=="compute")].resources.limits.cpu,MEM_REQ:.spec.containers[?(@.name=="compute")].resources.requests.memory,MEM_LIM:.spec.containers[?(@.name=="compute")].resources.limits.memory' 2>&1
    sec "SR-IOV capability of the NICs visible here (sriov_totalvfs missing = no SR-IOV)"
    for d in /sys/class/net/*/device/sriov_totalvfs; do [ -e "$d" ] && echo "$d: $(cat "$d")"; done; echo "(end of list)"
    lspci 2>/dev/null | grep -i ethernet || echo "lspci: nothing / not installed"
    sec "Open5GS direct namespace: pods with their QoS class and CPU/memory limits"
    $KUBECTL -n open5gs-direct get pods -o custom-columns='POD:.metadata.name,QOS:.status.qosClass,CPU_LIM:.spec.containers[0].resources.limits.cpu,MEM_LIM:.spec.containers[0].resources.limits.memory,NODE:.spec.nodeName' 2>&1
  else
    sec "Open5GS pods with QoS class and limits"
    $KUBECTL -n open5gs get pods -o custom-columns='POD:.metadata.name,QOS:.status.qosClass,CPU_LIM:.spec.containers[0].resources.limits.cpu,MEM_LIM:.spec.containers[0].resources.limits.memory,NODE:.spec.nodeName' 2>&1
  fi
  sec "helm / chart versions"; helm list -A 2>&1 | head
} 2>&1 | tee "$F"
echo; echo "saved $F"
