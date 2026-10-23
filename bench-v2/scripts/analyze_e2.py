#!/usr/bin/env python3
"""E2 (minimal): user-plane throughput (run_e2.sh), nested vs direct.

usage: analyze_e2.py <e2 results dir> <output dir>
  e.g. analyze_e2.py ~/Desktop/thesis/bench-v2-results/e2 ~/Desktop/thesis/bench-v2-report/e2

reads : <e2>/<variant>/rep<k>/iperf_<path>_<test>.json, ping_<path>.txt, cadvisor_e2.csv
writes: <out>/tables/e2_runs.csv, e2_throughput.tex, e2_cpu.tex, <out>/figs/e2_throughput.png

Paths: "pod" = from the PacketRusher pod's tools container over the pod network only (no 5G, the generator check F7);
"pr" = the same container, bound to the UE address: GTP tunnel -> UPF -> iperf3 server.
CPU: from the cAdvisor counters, per container, over the window of each path's tests (first start to last end, taken from
the iperf3 timestamps); "throttled" = throttled CFS periods / all periods in that window.
"""
import csv, glob, json, os, re, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

E2, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
PATHS = {"pod": "pod network (no 5G)", "pr": "UE tunnel + UPF"}
CONTAINERS = ["open5gs-upf", "packetrusher", "tools", "iperf"]
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)


def test_order(t):
    m = re.fullmatch(r"udp_b(\d+)M", t)
    return (0, t) if t.startswith("tcp") else (1, int(m.group(1)) if m else 0)


rows = []                                  # one per (variant, rep, path, test)
windows = defaultdict(lambda: [None, None])  # (variant, rep, path) -> [start, end] in epoch seconds
ping = {}                                  # (variant, rep, path) -> avg rtt ms
cpu = {}                                   # (variant, rep, path, container) -> (cores, throttled share)
for v in V:
    for repdir in sorted(glob.glob(os.path.join(E2, v, "rep*"))):
        rep = os.path.basename(repdir)
        for f in sorted(glob.glob(os.path.join(repdir, "iperf_*.json"))):
            m = re.fullmatch(r"iperf_(pod|pr)_(.+)\.json", os.path.basename(f))
            if not m:
                continue
            path, test = m.groups()
            try:
                o = json.load(open(f))
            except ValueError:
                print("unreadable:", f); continue
            if "error" in o or "end" not in o:
                print("iperf3 error in", f, o.get("error")); continue
            e = o["end"]
            if test.startswith("tcp"):
                mbps, extra = e["sum_received"]["bits_per_second"] / 1e6, e["sum_sent"].get("retransmits", 0)
            else:
                mbps, extra = e.get("sum_received", e["sum"])["bits_per_second"] / 1e6, e["sum"].get("lost_percent", 0.0)
            rows.append({"variant": v, "rep": rep, "path": path, "test": test, "mbps": mbps, "extra": extra})
            t0 = o["start"]["timestamp"]["timesecs"]
            t1 = t0 + o["start"]["test_start"]["duration"]
            w = windows[(v, rep, path)]
            w[0] = t0 if w[0] is None else min(w[0], t0)
            w[1] = t1 if w[1] is None else max(w[1], t1)
        for path in PATHS:
            p = os.path.join(repdir, f"ping_{path}.txt")
            if os.path.exists(p):
                mm = re.search(r"= [\d.]+/([\d.]+)/", open(p).read())
                if mm:
                    ping[(v, rep, path)] = float(mm.group(1))
        cf = os.path.join(repdir, "cadvisor_e2.csv")
        if os.path.exists(cf):
            series = defaultdict(list)     # (container, metric) -> [(ts, value)]
            for r in csv.DictReader(open(cf)):
                try:
                    series[(r["container"], r["metric"])].append((float(r["ts"]), float(r["value"])))
                except (ValueError, KeyError):
                    continue
            for path in PATHS:
                t0, t1 = windows.get((v, rep, path), [None, None])
                if t0 is None:
                    continue
                for c in CONTAINERS:
                    def delta(metric):
                        pts = sorted(x for x in series.get((c, metric), []) if t0 - 5 <= x[0] <= t1 + 5)
                        return (pts[-1][1] - pts[0][1], pts[-1][0] - pts[0][0]) if len(pts) >= 2 else (None, None)
                    used, dt = delta("container_cpu_usage_seconds_total")
                    per, _ = delta("container_cpu_cfs_periods_total")
                    thr, _ = delta("container_cpu_cfs_throttled_periods_total")
                    if used is not None and dt:
                        cpu[(v, rep, path, c)] = (used / dt, (thr / per * 100) if per else 0.0)

with open(os.path.join(OUT, "tables", "e2_runs.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["variant", "rep", "path", "test", "mbit_s", "tcp_retransmits_or_udp_loss_pct"])
    for r in sorted(rows, key=lambda r: (r["variant"], r["rep"], r["path"], test_order(r["test"]))):
        w.writerow([r["variant"], r["rep"], r["path"], r["test"], round(r["mbps"], 1), round(r["extra"], 3)])


def vals(v, path, test, key="mbps"):
    return [r[key] for r in rows if r["variant"] == v and r["path"] == path and r["test"] == test]


def fmt(xs, f="{:.0f}"):
    return "--" if not xs else f.format(st.median(xs)) + " (" + f.format(min(xs)) + "--" + f.format(max(xs)) + ")"


tests = sorted({r["test"] for r in rows}, key=test_order)
lines = [r"\begin{tabular}{l l ll ll}", r"\toprule",
         r" & & \multicolumn{2}{c}{Mbit/s} & \multicolumn{2}{c}{TCP retransmits / UDP loss (\%)} \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}", r"path & test & nested & direct & nested & direct \\", r"\midrule"]
for path in PATHS:
    for t in tests:
        f2 = "{:.0f}" if t.startswith("tcp") else "{:.2f}"
        lines.append(f"{path} & {t.replace('_', ' ')} & {fmt(vals('nested', path, t))} & {fmt(vals('direct', path, t))} & "
                     f"{fmt(vals('nested', path, t, 'extra'), f2)} & {fmt(vals('direct', path, t, 'extra'), f2)}" + r" \\")
    pn = [x for (v, _, p), x in ping.items() if v == "nested" and p == path]
    pd = [x for (v, _, p), x in ping.items() if v == "direct" and p == path]
    lines.append(f"{path} & ping (ms) & {fmt(pn, '{:.2f}')} & {fmt(pd, '{:.2f}')} & & " + r"\\")
    lines.append(r"\midrule" if path == "pod" else "")
with open(os.path.join(OUT, "tables", "e2_throughput.tex"), "w") as f:
    f.write("\n".join(l for l in lines if l) + "\n" + r"\bottomrule" + "\n" + r"\end{tabular}" + "\n")

lines = [r"\begin{tabular}{l l ll ll}", r"\toprule",
         r" & & \multicolumn{2}{c}{CPU used (cores)} & \multicolumn{2}{c}{throttled periods (\%)} \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}", r"path & container & nested & direct & nested & direct \\", r"\midrule"]
for path in PATHS:
    for c in CONTAINERS:
        g = lambda v, i: [x[i] for (vv, _, p, cc), x in cpu.items() if vv == v and p == path and cc == c]
        if not (g("nested", 0) or g("direct", 0)):
            continue
        lines.append(f"{path} & {c} & {fmt(g('nested', 0), '{:.2f}')} & {fmt(g('direct', 0), '{:.2f}')} & "
                     f"{fmt(g('nested', 1))} & {fmt(g('direct', 1))}" + r" \\")
with open(os.path.join(OUT, "tables", "e2_cpu.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
for ax, path in zip(axes, PATHS):
    for i, v in enumerate(V):
        xs = [j + (i - 0.5) * 0.35 for j in range(len(tests))]
        med = [st.median(vals(v, path, t)) if vals(v, path, t) else 0 for t in tests]
        ax.bar(xs, med, width=0.35, color=COLOR[v], label=v)
        for x, t in zip(xs, tests):
            ax.plot([x] * len(vals(v, path, t)), vals(v, path, t), "k.", ms=4)
    ax.set_xticks(range(len(tests)), [t.replace("_", " ") for t in tests], rotation=20)
    ax.set_ylabel("Mbit/s (bar: median, dots: repetitions)"); ax.set_title(PATHS[path]); ax.grid(axis="y", alpha=0.3)
    ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "e2_throughput.png"), dpi=150)
print(f"{len(rows)} iperf3 results, {len(ping)} ping files, {len(cpu)} CPU windows -> {OUT}")
