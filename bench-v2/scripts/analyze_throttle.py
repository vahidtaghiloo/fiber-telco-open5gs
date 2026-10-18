#!/usr/bin/env python3
"""E4 (short version): CPU use and CPU throttling of every container during a PacketRusher run, nested vs direct.

usage: analyze_throttle.py <results dir> <output dir>
  e.g. analyze_throttle.py ~/Desktop/thesis/bench-v2-results/e4lite ~/Desktop/thesis/bench-v2-report/e4lite

reads : <results>/<variant>/run<k>/cadvisor_pr1000.csv   (cadvisor_sample.sh)
writes: <out>/tables/throttle.csv, throttle.tex, <out>/figs/throttle.png

Per container and run, between the first and the last sample of that container:
  throttled share = increase of container_cpu_cfs_throttled_periods_total / increase of container_cpu_cfs_periods_total
                    (share of the 100-ms periods in which the container wanted more CPU than its limit allowed)
  throttled time  = increase of container_cpu_cfs_throttled_seconds_total
  CPU peak        = highest CPU use over one sampling interval (cores) = increase of cpu_usage_seconds / seconds
A container whose pod was replaced during the run (the AMF is restarted by the script) is summed over its pods.
"""
import csv, os, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
# limits from values/resources-fixed.yaml, values/ueransim-resources-fixed.yaml, scripts/manifests/packetrusher.yaml
LIMIT = {"open5gs-amf": 1.0, "open5gs-smf": 1.0, "mongodb": 1.5, "open5gs-scp": 0.75, "open5gs-udm": 0.5, "open5gs-udr": 0.5,
         "open5gs-ausf": 0.5, "open5gs-pcf": 0.5, "open5gs-nrf": 0.25, "open5gs-nssf": 0.25, "open5gs-bsf": 0.25,
         "open5gs-upf": 1.5, "packetrusher": 1.25, "tools": 0.25}
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)


def load(path):
    series = defaultdict(lambda: defaultdict(list))      # (pod, container) -> metric -> [(ts, value)]
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                series[(r["pod"], r["container"])][r["metric"]].append((float(r["ts"]), float(r["value"])))
            except ValueError:
                pass
    return series


def delta(points):
    points = sorted(points)
    return points[-1][1] - points[0][1] if len(points) > 1 else 0.0


def peak_rate(points):
    points = sorted(points)
    best = 0.0
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t1 > t0 and v1 >= v0:
            best = max(best, (v1 - v0) / (t1 - t0))
    return best


res = defaultdict(lambda: defaultdict(list))   # (variant, container) -> field -> [value per run]
runs = defaultdict(int)
for v in V:
    vdir = os.path.join(RES, v)
    if not os.path.isdir(vdir):
        continue
    for run in sorted(os.listdir(vdir)):
        path = os.path.join(vdir, run, "cadvisor_pr1000.csv")
        if not os.path.exists(path):
            continue
        runs[v] += 1
        per = defaultdict(lambda: defaultdict(float))
        for (pod, c), m in load(path).items():
            per[c]["periods"] += delta(m.get("container_cpu_cfs_periods_total", []))
            per[c]["throttled"] += delta(m.get("container_cpu_cfs_throttled_periods_total", []))
            per[c]["throttled_s"] += delta(m.get("container_cpu_cfs_throttled_seconds_total", []))
            per[c]["cpu_s"] += delta(m.get("container_cpu_usage_seconds_total", []))
            per[c]["peak"] = max(per[c]["peak"], peak_rate(m.get("container_cpu_usage_seconds_total", [])))
            mem = [x for _, x in m.get("container_memory_working_set_bytes", [])]
            per[c]["mem_mib"] = max(per[c]["mem_mib"], max(mem) / 2**20 if mem else 0)
        for c, x in per.items():
            res[(v, c)]["share"].append(x["throttled"] / x["periods"] * 100 if x["periods"] > 0 else 0.0)
            res[(v, c)]["throttled_s"].append(x["throttled_s"])
            res[(v, c)]["cpu_s"].append(x["cpu_s"])
            res[(v, c)]["peak"].append(x["peak"])
            res[(v, c)]["mem_mib"].append(x["mem_mib"])

containers = sorted({c for (_, c) in res}, key=lambda c: (-LIMIT.get(c, 0), c))
rows = []
for c in containers:
    row = [c, LIMIT.get(c, "")]
    for v in V:
        x = res.get((v, c), {})
        if not x:
            row += ["", "", "", "", ""]
            continue
        row += [round(st.median(x["share"]), 1), round(max(x["share"]), 1), round(st.median(x["throttled_s"]), 1),
                round(st.median(x["peak"]), 2), round(st.median(x["cpu_s"]), 1)]
    rows.append(row)
hdr = ["container", "limit_cpu"] + [f"{v}_{k}" for v in V for k in
                                    ("throttled_share_pct_median", "throttled_share_pct_max", "throttled_s_median", "cpu_peak_cores_median", "cpu_seconds_median")]
with open(os.path.join(OUT, "tables", "throttle.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(hdr); w.writerows(rows)

lines = [r"\begin{tabular}{l r rrr rrr}", r"\toprule",
         r" & & \multicolumn{3}{c}{nested} & \multicolumn{3}{c}{direct} \\", r"\cmidrule(lr){3-5}\cmidrule(lr){6-8}",
         r"container & limit & throttled \% & throttled s & CPU peak & throttled \% & throttled s & CPU peak \\", r"\midrule"]
for r in rows:
    def cell(i):
        return "--" if r[i] == "" else str(r[i])
    lines.append(f"{r[0].replace('_', chr(92) + '_')} & {r[1]} & {cell(2)} & {cell(4)} & {cell(5)} & {cell(7)} & {cell(9)} & {cell(10)}" + r" \\")
with open(os.path.join(OUT, "tables", "throttle.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

fig, axes = plt.subplots(1, 2, figsize=(13, 0.45 * len(containers) + 2), sharey=True)
y = list(range(len(containers)))
for i, v in enumerate(V):
    off = [p + (i - 0.5) * 0.38 for p in y]
    share = [st.median(res[(v, c)]["share"]) if (v, c) in res else 0 for c in containers]
    peak = [st.median(res[(v, c)]["peak"]) if (v, c) in res else 0 for c in containers]
    axes[0].barh(off, share, height=0.36, color=COLOR[v], alpha=0.85, label=f"{v} ({runs[v]} runs, median)")
    axes[1].barh(off, peak, height=0.36, color=COLOR[v], alpha=0.85, label=v)
axes[1].scatter([LIMIT.get(c, float("nan")) for c in containers], y, marker="|", s=300, color="black", label="CPU limit", zorder=3)
axes[0].set_yticks(y, containers); axes[0].invert_yaxis()
axes[0].set_xlabel("throttled periods (% of the 100-ms periods)"); axes[0].set_title("How often the CPU limit paused the container")
axes[1].set_xlabel("CPU cores (highest 5-s average)"); axes[1].set_title("CPU use at its peak, and the limit")
for ax in axes:
    ax.grid(axis="x", alpha=0.3)
axes[0].legend(loc="lower right"); axes[1].legend(loc="lower right")
fig.suptitle("PacketRusher, 1000 phones: CPU throttling per container")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "throttle.png"), dpi=150)
print("runs:", dict(runs))
for r in rows:
    print(r)
