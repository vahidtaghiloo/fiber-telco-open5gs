#!/usr/bin/env python3
"""E2b: user-plane throughput with the UPF CPU limit lowered (run_e2b.sh), nested vs direct.

usage: analyze_e2b.py <e2b results dir> <output dir>
  e.g. analyze_e2b.py ~/Desktop/thesis/bench-v2-results/e2b ~/Desktop/thesis/bench-v2-report/e2b

reads : <e2b>/<variant>/upf<cpu>/rep<k>/iperf_<path>_<test>.json and cadvisor_e2.csv
writes: <out>/tables/e2b_tests.csv, e2b_tcp.tex, e2b_udp.tex, e2b_cpu.tex, e2b_cpu.csv, <out>/figs/e2b_upf.png

Windows for the CPU counters: per repetition and path, one window for the TCP tests (start of P1 to end of P4) and one for
the UDP tests, taken from the iperf3 timestamps. The kubelet refreshes its counters every 10-15 s, so only samples where a
counter changed are used, and a window yields a value only if it contains at least two such samples.
"""
import csv, glob, json, os, re, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

E2B, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
CONTAINERS = ["open5gs-upf", "packetrusher", "tools", "iperf"]
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)


def millicores(name):              # "upf250m" -> 250, "upf1000m" -> 1000
    return int(re.search(r"upf(\d+)m", name).group(1))


def test_key(t):                   # tcp_P1, tcp_P4, udp_b50M, udp_b100M, ...
    m = re.fullmatch(r"udp_b(\d+)M", t)
    return (0, t) if t.startswith("tcp") else (1, int(m.group(1)))


tests = []                         # one row per iperf3 file
windows = defaultdict(lambda: [None, None])   # (variant, upf, rep, path, proto) -> [t0, t1]
cpu = {}                           # (variant, upf, rep, path, proto, container) -> (cores, throttled %)
for v in V:
    for upfdir in sorted(glob.glob(os.path.join(E2B, v, "upf*m")), key=millicores):
        upf = millicores(os.path.basename(upfdir))
        for repdir in sorted(glob.glob(os.path.join(upfdir, "rep*"))):
            rep = os.path.basename(repdir)
            for f in glob.glob(os.path.join(repdir, "iperf_*.json")):
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
                    mbps, loss, retr = e["sum_received"]["bits_per_second"] / 1e6, None, e["sum_sent"].get("retransmits", 0)
                else:
                    mbps, loss, retr = e.get("sum_received", e["sum"])["bits_per_second"] / 1e6, e["sum"].get("lost_percent", 0.0), None
                tests.append(dict(variant=v, upf=upf, rep=rep, path=path, test=test, mbps=mbps, loss=loss, retr=retr))
                t0 = o["start"]["timestamp"]["timesecs"]; t1 = t0 + o["start"]["test_start"]["duration"]
                w = windows[(v, upf, rep, path, test[:3])]
                w[0] = t0 if w[0] is None else min(w[0], t0)
                w[1] = t1 if w[1] is None else max(w[1], t1)
            cf = os.path.join(repdir, "cadvisor_e2.csv")
            if not os.path.exists(cf):
                continue
            series = defaultdict(list)
            for r in csv.DictReader(open(cf)):
                try:
                    series[(r["container"], r["metric"])].append((float(r["ts"]), float(r["value"])))
                except (ValueError, KeyError):
                    continue
            for (vv, uu, rr, path, proto), (t0, t1) in list(windows.items()):
                if (vv, uu, rr) != (v, upf, rep):
                    continue
                for c in CONTAINERS:
                    def delta(metric):
                        pts = sorted(x for x in series.get((c, metric), []) if t0 - 3 <= x[0] <= t1 + 3)
                        pts = [p for i, p in enumerate(pts) if i == 0 or p[1] != pts[i - 1][1]]   # keep refreshes only
                        return (pts[-1][1] - pts[0][1], pts[-1][0] - pts[0][0]) if len(pts) >= 2 else (None, None)
                    used, dt = delta("container_cpu_usage_seconds_total")
                    per, _ = delta("container_cpu_cfs_periods_total")
                    thr, _ = delta("container_cpu_cfs_throttled_periods_total")
                    if used is not None and dt and per:
                        cpu[(v, upf, rep, path, proto, c)] = (used / dt, 100 * (thr or 0) / per)

with open(os.path.join(OUT, "tables", "e2b_tests.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["variant", "upf_millicores", "rep", "path", "test", "mbit_s", "udp_loss_pct", "tcp_retransmits"])
    for r in sorted(tests, key=lambda r: (r["variant"], r["upf"], r["rep"], r["path"], test_key(r["test"]))):
        w.writerow([r["variant"], r["upf"], r["rep"], r["path"], r["test"], round(r["mbps"], 1),
                    "" if r["loss"] is None else round(r["loss"], 3), "" if r["retr"] is None else r["retr"]])
with open(os.path.join(OUT, "tables", "e2b_cpu.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["variant", "upf_millicores", "rep", "path", "proto", "container", "cores", "throttled_pct"])
    for k, (c, t) in sorted(cpu.items()):
        w.writerow(list(k) + [round(c, 3), round(t, 1)])


def vals(v, upf, path, test, key="mbps"):
    return [r[key] for r in tests if (r["variant"], r["upf"], r["path"], r["test"]) == (v, upf, path, test) and r[key] is not None]


def cvals(v, upf, path, proto, c, i):
    return [x[i] for (vv, uu, _, pp, pr, cc), x in cpu.items() if (vv, uu, pp, pr, cc) == (v, upf, path, proto, c)]


def fmt(xs, f="{:.0f}"):
    return "--" if not xs else f.format(st.median(xs)) + " (" + f.format(min(xs)) + "--" + f.format(max(xs)) + ")"


upfs = sorted({r["upf"] for r in tests})
udp_tests = sorted({r["test"] for r in tests if r["test"].startswith("udp")}, key=test_key)

lines = [r"\begin{tabular}{r ll ll rr}", r"\toprule",
         r" & \multicolumn{2}{c}{TCP, 1 stream (Mbit/s)} & \multicolumn{2}{c}{TCP, 4 streams (Mbit/s)} & \multicolumn{2}{c}{Mbit/s per UPF core$^{*}$} \\",
         r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}",
         r"UPF limit (CPU) & nested & direct & nested & direct & nested & direct \\", r"\midrule"]
for u in upfs:
    # Mbit/s per UPF core only where the UPF was the limit (median throttled share during the TCP tests at least 50 %)
    per_core = [f"{st.median(vals(v, u, 'pr', 'tcp_P4')) / (u / 1000):.0f}"
                if vals(v, u, "pr", "tcp_P4") and cvals(v, u, "pr", "tcp", "open5gs-upf", 1) and st.median(cvals(v, u, "pr", "tcp", "open5gs-upf", 1)) >= 50
                else "--" for v in V]
    lines.append(f"{u / 1000:g} & {fmt(vals('nested', u, 'pr', 'tcp_P1'))} & {fmt(vals('direct', u, 'pr', 'tcp_P1'))} & "
                 f"{fmt(vals('nested', u, 'pr', 'tcp_P4'))} & {fmt(vals('direct', u, 'pr', 'tcp_P4'))} & {per_core[0]} & {per_core[1]}" + r" \\")
with open(os.path.join(OUT, "tables", "e2b_tcp.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

lines = [r"\begin{tabular}{r r ll ll}", r"\toprule",
         r" & & \multicolumn{2}{c}{received (Mbit/s)} & \multicolumn{2}{c}{loss (\%)} \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}", r"UPF limit & offered & nested & direct & nested & direct \\", r"\midrule"]
for u in upfs:
    for t in udp_tests:
        lines.append(f"{u / 1000:g} & {t[5:]} & {fmt(vals('nested', u, 'pr', t))} & {fmt(vals('direct', u, 'pr', t))} & "
                     f"{fmt(vals('nested', u, 'pr', t, 'loss'), '{:.1f}')} & {fmt(vals('direct', u, 'pr', t, 'loss'), '{:.1f}')}" + r" \\")
with open(os.path.join(OUT, "tables", "e2b_udp.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

lines = [r"\begin{tabular}{r l ll ll}", r"\toprule",
         r" & & \multicolumn{2}{c}{CPU used (cores)} & \multicolumn{2}{c}{throttled periods (\%)} \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}", r"UPF limit & container & nested & direct & nested & direct \\", r"\midrule"]
for u in upfs:
    for c in CONTAINERS:
        lines.append(f"{u / 1000:g} & {c} & {fmt(cvals('nested', u, 'pr', 'tcp', c, 0), '{:.2f}')} & {fmt(cvals('direct', u, 'pr', 'tcp', c, 0), '{:.2f}')} & "
                     f"{fmt(cvals('nested', u, 'pr', 'tcp', c, 1))} & {fmt(cvals('direct', u, 'pr', 'tcp', c, 1))}" + r" \\")
    lines.append(r"\midrule" if u != upfs[-1] else "")
with open(os.path.join(OUT, "tables", "e2b_cpu.tex"), "w") as f:
    f.write("\n".join(l for l in lines if l) + "\n" + r"\bottomrule" + "\n" + r"\end{tabular}" + "\n")

fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
xs = [u / 1000 for u in upfs]
for v in V:
    for test, ls in (("tcp_P1", "--"), ("tcp_P4", "-")):
        med = [st.median(vals(v, u, "pr", test)) if vals(v, u, "pr", test) else float("nan") for u in upfs]
        axes[0].plot(xs, med, ls, color=COLOR[v], marker="o", label=f"{v}, TCP {test[-2:]}")
        for u in upfs:
            axes[0].plot([u / 1000] * len(vals(v, u, "pr", test)), vals(v, u, "pr", test), ".", color=COLOR[v], alpha=0.4)
    thr = [st.median(cvals(v, u, "pr", "tcp", "open5gs-upf", 1)) if cvals(v, u, "pr", "tcp", "open5gs-upf", 1) else float("nan") for u in upfs]
    used = [st.median(cvals(v, u, "pr", "tcp", "open5gs-upf", 0)) if cvals(v, u, "pr", "tcp", "open5gs-upf", 0) else float("nan") for u in upfs]
    axes[1].plot(xs, thr, "-", color=COLOR[v], marker="o", label=f"{v}: UPF throttled periods (%)")
    axes[1].plot(xs, [100 * a / b for a, b in zip(used, xs)], ":", color=COLOR[v], marker="s", label=f"{v}: UPF CPU used (% of limit)")
axes[0].set_xlabel("UPF CPU limit (cores)"); axes[0].set_ylabel("TCP throughput through the UPF (Mbit/s)")
axes[0].set_title("Throughput against the UPF limit"); axes[0].set_xticks(xs); axes[0].grid(alpha=0.3); axes[0].legend(fontsize=8)
axes[1].set_xlabel("UPF CPU limit (cores)"); axes[1].set_ylabel("%"); axes[1].set_ylim(0, 105); axes[1].set_xticks(xs)
axes[1].set_title("UPF during the TCP tests"); axes[1].grid(alpha=0.3); axes[1].legend(fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "e2b_upf.png"), dpi=150)
fig.savefig(os.path.join(OUT, "figs", "e2b_upf.pdf"))
print(f"{len(tests)} iperf3 results, {len(cpu)} CPU windows -> {OUT}")
