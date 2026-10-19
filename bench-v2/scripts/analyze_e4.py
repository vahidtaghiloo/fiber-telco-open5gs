#!/usr/bin/env python3
"""E4 (short version): CPU throttling per container during PacketRusher runs with 1000 phones, two NRF limits.

usage: analyze_e4.py <results root> <output dir>
  e.g. analyze_e4.py ~/Desktop/thesis/bench-v2-results ~/Desktop/thesis/bench-v2-report/e7

reads : <root>/e4lite/<variant>/run<k>/        NRF limit 0.25 CPU (as in E7)
        <root>/e4lite-nrf750/<variant>/run<k>/ NRF limit 0.75 CPU
        in each run: cadvisor_pr1000.csv (cadvisor_sample.sh) and pr/pr_N1000_r01.json (PacketRusher report)
writes: <out>/tables/e4_runs.tex, e4_throttle.tex, e4_throttle.csv, <out>/figs/e4_throttle.png

Per container and run, between its first and last sample: throttled share = increase of throttled periods / increase of periods,
and CPU-seconds = increase of cpu_usage_seconds. Containers whose pod was replaced during the run (AMF) are summed over pods.
No per-interval CPU rates are computed: the kubelet refreshes these counters only every 10-15 s.
A run in which no registration succeeded is a broken core, not a load test; it is listed but left out of the throttling figures.
"""
import csv, json, os, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
CONFIGS = [("e4lite", "NRF 0.25"), ("e4lite-nrf750", "NRF 0.75")]
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
SHOW = ["open5gs-nrf", "open5gs-udm", "open5gs-pcf", "open5gs-scp", "mongodb", "open5gs-udr", "open5gs-ausf", "open5gs-amf",
        "open5gs-smf", "open5gs-upf", "packetrusher"]
LIMIT = {"open5gs-amf": "1", "open5gs-smf": "1", "mongodb": "1.5", "open5gs-scp": "0.75", "open5gs-udm": "0.5", "open5gs-udr": "0.5",
         "open5gs-ausf": "0.5", "open5gs-pcf": "0.5", "open5gs-nrf": "0.25 / 0.75", "open5gs-upf": "1.5", "packetrusher": "1.25"}
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)


def delta(pts):
    pts = sorted(pts)
    return pts[-1][1] - pts[0][1] if len(pts) > 1 else 0.0


runs_table = []
thr = defaultdict(lambda: defaultdict(list))   # (config, variant, container) -> {"share": [...], "cpu": [...]}
for cdir, clabel in CONFIGS:
    for v in V:
        vdir = os.path.join(ROOT, cdir, v)
        for run in sorted(x for x in os.listdir(vdir) if x.startswith("run")) if os.path.isdir(vdir) else []:
            rp = os.path.join(vdir, run, "pr", "pr_N1000_r01.json")
            cp = os.path.join(vdir, run, "cadvisor_pr1000.csv")
            if not (os.path.exists(rp) and os.path.exists(cp)):
                continue
            rep = json.load(open(rp))
            p = {x["procedure"]: x for x in rep["procedures"]}
            reg, pdu = p["registration"], p["pdu_session_establishment"]
            broken = reg["success"] == 0
            runs_table.append([clabel, v, run, rep["started_at"][11:16], reg["success"], reg["started"], reg["failure"],
                               round(reg["latency_seconds_mean"] * 1000), round(pdu["latency_seconds_mean"] * 1000), broken])
            if broken:
                continue
            series = defaultdict(lambda: defaultdict(list))
            with open(cp) as f:
                for r in csv.DictReader(f):
                    try:
                        series[(r["pod"], r["container"])][r["metric"]].append((float(r["ts"]), float(r["value"])))
                    except ValueError:
                        pass
            per = defaultdict(lambda: defaultdict(float))
            for (pod, c), m in series.items():
                per[c]["periods"] += delta(m.get("container_cpu_cfs_periods_total", []))
                per[c]["throttled"] += delta(m.get("container_cpu_cfs_throttled_periods_total", []))
                per[c]["cpu"] += delta(m.get("container_cpu_usage_seconds_total", []))
            for c, x in per.items():
                thr[(clabel, v, c)]["share"].append(x["throttled"] / x["periods"] * 100 if x["periods"] > 0 else 0.0)
                thr[(clabel, v, c)]["cpu"].append(x["cpu"])

# table: runs
lines = [r"\begin{tabular}{llll rrr rr}", r"\toprule",
         r"NRF limit & variant & run & start & registered & rejected & of & reg.\ mean (ms) & PDU mean (ms) \\", r"\midrule"]
for c, v, run, t, ok, started, fail, rm, pm, broken in runs_table:
    note = r" \textit{(core broken)}" if broken else ""
    lines.append(f"{c} & {v} & {run.replace('run', '')} & {t} & {ok} & {fail} & {started} & "
                 + ("--" if broken else str(rm)) + " & " + ("--" if broken else str(pm)) + note + r" \\")
with open(os.path.join(OUT, "tables", "e4_runs.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

# table: throttling
def cell(key, fld, fmt):
    vals = thr.get(key, {}).get(fld, [])
    if not vals:
        return "--"
    if len(vals) == 1:
        return fmt.format(vals[0])
    return fmt.format(st.median(vals)) + " (" + fmt.format(min(vals)) + "--" + fmt.format(max(vals)) + ")"

rows, csvrows = [], []
for c in SHOW:
    cells = []
    for _, cl in CONFIGS:
        for v in V:
            cells.append(cell((cl, v, c), "share", "{:.0f}"))
    for v in V:
        cells.append(cell(("NRF 0.75", v, c), "cpu", "{:.1f}"))
    rows.append(f"{c.replace('open5gs-', '')} & {LIMIT.get(c, '')} & " + " & ".join(cells) + r" \\")
    csvrows.append([c, LIMIT.get(c, "")] + cells)
lines = [r"\begin{tabular}{l l rr rr rr}", r"\toprule",
         r" & & \multicolumn{4}{c}{throttled periods, \% (median and range of runs)} & \multicolumn{2}{c}{CPU-seconds, NRF 0.75} \\",
         r"\cmidrule(lr){3-6}\cmidrule(lr){7-8}",
         r" & & \multicolumn{2}{c}{NRF 0.25} & \multicolumn{2}{c}{NRF 0.75} & & \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}",
         r"container & CPU limit & nested & direct & nested & direct & nested & direct \\", r"\midrule"] + rows
with open(os.path.join(OUT, "tables", "e4_throttle.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")
with open(os.path.join(OUT, "tables", "e4_throttle.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["container", "limit"] + [f"{cl}_{v}_throttled_pct" for _, cl in CONFIGS for v in V] + [f"NRF0.75_{v}_cpu_s" for v in V])
    w.writerows(csvrows)

# figure: throttled share per container, both configs
fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
y = list(range(len(SHOW)))
for ax, (_, cl) in zip(axes, CONFIGS):
    for i, v in enumerate(V):
        med = [st.median(thr[(cl, v, c)]["share"]) if thr.get((cl, v, c)) else 0 for c in SHOW]
        lo = [m - min(thr[(cl, v, c)]["share"]) if thr.get((cl, v, c)) else 0 for m, c in zip(med, SHOW)]
        hi = [max(thr[(cl, v, c)]["share"]) - m if thr.get((cl, v, c)) else 0 for m, c in zip(med, SHOW)]
        n = max((len(thr[(cl, v, c)]["share"]) for c in SHOW if thr.get((cl, v, c))), default=0)
        ax.barh([p + (i - 0.5) * 0.38 for p in y], med, height=0.36, xerr=[lo, hi], color=COLOR[v], alpha=0.85,
                error_kw={"capsize": 2, "lw": 1}, label=f"{v} ({n} run{'s' if n != 1 else ''})")
    ax.set_title(f"{cl}: share of periods throttled")
    ax.set_xlabel("% of the 100-ms periods in which the CPU limit paused the container")
    ax.grid(axis="x", alpha=0.3)
    ax.legend(loc="lower right")
axes[0].set_yticks(y, [c.replace("open5gs-", "") for c in SHOW])
axes[0].invert_yaxis()
fig.suptitle("PacketRusher, 1000 phones: CPU throttling per container (bars: median, lines: range of runs)")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "e4_throttle.png"), dpi=150)
for r in runs_table:
    print(r)
for r in csvrows:
    print(r)
