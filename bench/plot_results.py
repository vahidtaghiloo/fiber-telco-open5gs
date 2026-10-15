#!/usr/bin/env python3
"""Draw the thesis figures from the CSVs written by parse_results.py.

usage: plot_results.py <results_root> <out_dir> <variant> [<variant> ...]
       plot_results.py ~/bench-results figs nested direct

Needs matplotlib (pip install matplotlib). Missing inputs are skipped, so you can plot with only one variant.
Figures: pr_control_plane.png (PacketRusher)  ue_latency.png  ue_attach_percentiles.png  ue_total.png  throughput.png  rtt.png  resources_layers_<variant>.png  resources.png (older, pod sums only)
"""
import csv, os, statistics, sys
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.text

COLORS = ["#1f77b4", "#d95f02", "#2ca02c", "#7570b3", "#e7298a"]
PATH_LABEL = {"pod": "pod-to-pod", "ue": "UERANSIM UE via UPF", "pr": "PacketRusher UE via UPF"}
# data-plane figures: the colour says WHICH PATH, the style says WHICH VARIANT (first variant solid, the others hatched / dashed)
PATH_COLOR = {"pod": "#8c8c8c", "ue": "#9467bd", "pr": "#2ca02c"}
PATH_SHORT = {"pod": "pod-to-pod", "ue": "UERANSIM\nUE", "pr": "PacketRusher\nUE"}
HATCHES = ["", "//", "xx"]
LINESTYLES = ["-", "--", ":"]


def read(root, variant, name):
    p = os.path.join(root, variant, name)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return list(csv.DictReader(f))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def save(fig, out, name):
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, name)
    k = float(os.environ.get("PLOT_FONT_SCALE", "1"))
    if k != 1:        # report mode: figures are shrunk to the page width, so every text is enlarged beforehand
        fig.canvas.draw()
        for t in fig.findobj(matplotlib.text.Text):
            t.set_fontsize(t.get_fontsize() * k)
    fig.tight_layout()
    fig.savefig(p, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("wrote", p)


def grouped_box(ax, data, variants, xs, ylabel, title):
    """data[variant][x] -> list of values"""
    width = 0.8 / max(len(variants), 1)
    for i, v in enumerate(variants):
        pos, vals = [], []
        for k, x in enumerate(xs):
            d = data[v].get(x, [])
            if d:
                pos.append(k + (i - (len(variants) - 1) / 2) * width)
                vals.append(d)
        if not vals:
            continue
        bp = ax.boxplot(vals, positions=pos, widths=width * 0.9, patch_artist=True, showfliers=False)
        for box in bp["boxes"]:
            box.set(facecolor=COLORS[i % len(COLORS)], alpha=0.6)
        for med in bp["medians"]:
            med.set(color="black")
        ax.plot([], [], color=COLORS[i % len(COLORS)], lw=8, alpha=0.6, label=v)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels([str(x) for x in xs])
    ax.set_xlabel("number of UEs")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)


def fig_ue(root, out, variants):
    rows = {v: read(root, v, "ue_times.csv") for v in variants}
    ns = sorted({int(r["n"]) for v in variants for r in rows[v]})
    if not ns:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, col, title in ((axes[0], "reg_ms", "Registration latency per UE"),
                           (axes[1], "pdu_ms", "PDU session establishment latency per UE")):
        data = {v: defaultdict(list) for v in variants}
        for v in variants:
            for r in rows[v]:
                y = num(r[col])
                if y is not None:
                    data[v][int(r["n"])].append(y)
        grouped_box(ax, data, variants, ns, "milliseconds", title)
    axes[0].legend()
    save(fig, out, "ue_latency.png")

    # attach latency per UE (registration + PDU session set-up): median, p90 and max against N
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for i, v in enumerate(variants):
        by = defaultdict(list)
        for r in rows[v]:
            a, b = num(r["reg_ms"]), num(r["pdu_ms"])
            if a is not None and b is not None:
                by[int(r["n"])].append(a + b)
        xs = sorted(by)
        if not xs:
            continue
        c = COLORS[i % len(COLORS)]
        q = lambda vals, f: sorted(vals)[int(f * (len(vals) - 1))]
        ax.plot(xs, [statistics.median(by[x]) for x in xs], "-o", color=c, label=f"{v}: median")
        ax.plot(xs, [q(by[x], 0.9) for x in xs], "--s", color=c, label=f"{v}: p90")
        ax.plot(xs, [max(by[x]) for x in xs], ":^", color=c, label=f"{v}: max")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(ns)
    ax.set_xticklabels([str(n) for n in ns])
    ax.set_xlabel("number of UEs started together")
    ax.set_ylabel("attach latency per UE (ms)")
    ax.set_title("Registration + PDU session set-up per UE")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=7)
    save(fig, out, "ue_attach_percentiles.png")

    # time until the last UE has its PDU session, per run
    fig, ax = plt.subplots(figsize=(6, 4))
    for i, v in enumerate(variants):
        runs = read(root, v, "ue_runs.csv")
        by = defaultdict(list)
        for r in runs:
            y = num(r["total_ms"])
            if y is not None:
                by[int(r["n"])].append(y / 1000)
        xs = sorted(by)
        if not xs:
            continue
        med = [statistics.median(by[x]) for x in xs]
        lo = [m - min(by[x]) for m, x in zip(med, xs)]
        hi = [max(by[x]) - m for m, x in zip(med, xs)]
        ax.errorbar(xs, med, yerr=[lo, hi], marker="o", capsize=3, color=COLORS[i % len(COLORS)], label=v)
    ax.set_xlabel("number of UEs")
    ax.set_ylabel("seconds until all PDU sessions are up")
    ax.set_title("Control-plane scaling (median, min-max)")
    ax.grid(alpha=0.3)
    ax.legend()
    save(fig, out, "ue_total.png")


def fig_throughput(root, out, variants):
    rows = {v: read(root, v, "iperf.csv") for v in variants}
    if not any(rows.values()):
        return
    paths = [p for p in ("pod", "ue", "pr") if any(r["path"] == p for v in variants for r in rows[v])]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))

    # TCP: grouped bars (group = 1 or 4 streams, bar = path x variant), LOG axis because pod-to-pod on one node is far faster
    tags = ["P1", "P4"]
    series = [(p, v) for p in paths for v in variants]
    width = 0.8 / max(len(series), 1)
    for i, (p, v) in enumerate(series):
        vi = variants.index(v)
        means, errs = [], []
        for t in tags:
            vals = [num(r["mbps"]) for r in rows[v] if r["path"] == p and r["proto"] == "tcp" and r["tag"] == t]
            vals = [x for x in vals if x is not None]
            means.append(statistics.mean(vals) if vals else 0)
            errs.append(statistics.pstdev(vals) if len(vals) > 1 else 0)
        axes[0].bar([k + (i - (len(series) - 1) / 2) * width for k in range(len(tags))], means, width * 0.92,
                    yerr=errs, capsize=2, color=PATH_COLOR[p], hatch=HATCHES[vi % len(HATCHES)], edgecolor="white",
                    alpha=0.9, label=f"{v}: {PATH_LABEL[p]}")
    axes[0].set_yscale("log")
    axes[0].set_xticks(range(len(tags)))
    axes[0].set_xticklabels(["TCP, 1 stream", "TCP, 4 streams"])
    axes[0].set_ylabel("Mbit/s (log scale)")
    axes[0].set_title("TCP throughput (mean, std)")
    axes[0].grid(axis="y", alpha=0.3, which="both")
    axes[0].legend(fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2)

    # UDP loss
    offered = sorted({int(r["tag"][1:-1]) for v in variants for r in rows[v] if r["proto"] == "udp"})
    for p in paths:
        for vi, v in enumerate(variants):
            loss = []
            for o in offered:
                sel = [num(r["loss_pct"]) for r in rows[v]
                       if r["path"] == p and r["proto"] == "udp" and r["tag"] == f"b{o}M" and num(r["loss_pct"]) is not None]
                loss.append(statistics.mean(sel) if sel else float("nan"))
            axes[1].plot(offered, loss, marker="o", color=PATH_COLOR[p], linestyle=LINESTYLES[vi % len(LINESTYLES)],
                         label=f"{v}: {PATH_LABEL[p]}")
    axes[1].set_xlabel("offered load (Mbit/s)")
    axes[1].set_ylabel("packet loss (%)")
    axes[1].set_title("UDP packet loss")
    axes[1].grid(alpha=0.3)
    axes[1].legend(fontsize=9, loc="upper left")
    save(fig, out, "throughput.png")


def fig_rtt(root, out, variants):
    labels, data, colors, hatches = [], [], [], []
    for vi, v in enumerate(variants):
        rows = read(root, v, "ping.csv")
        for p in ("pod", "ue", "pr"):
            vals = [num(r["rtt_ms"]) for r in rows if r["path"] == p]
            vals = [x for x in vals if x is not None]
            if vals:
                labels.append(f"{v}\n{PATH_SHORT[p]}")
                data.append(vals)
                colors.append(PATH_COLOR[p])
                hatches.append(HATCHES[vi % len(HATCHES)])
    if not data:
        return
    fig, ax = plt.subplots(figsize=(1.35 * len(data) + 1.5, 4.6))
    bp = ax.boxplot(data, patch_artist=True, showfliers=False)
    ax.set_xticks(range(1, len(data) + 1))
    ax.set_xticklabels(labels, fontsize=9)
    for box, c, h in zip(bp["boxes"], colors, hatches):
        box.set(facecolor=c, alpha=0.75, hatch=h)
    for med in bp["medians"]:
        med.set(color="black", linewidth=1.5)
    ax.set_ylabel("RTT (ms)")
    ax.set_title("Round-trip time to the data-network server (200 echoes per box)")
    ax.grid(axis="y", alpha=0.3)
    save(fig, out, "rtt.png")


def fig_resources(root, out, variants):
    # total CPU (millicores) of all pods in each scope, per phase: how much the layers cost
    phases = ["idle", "ue", "iperf"]
    entries = []
    for v in variants:
        d = os.path.join(root, v, "res")
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not (f.startswith("res_") and f.endswith(".csv")):
                continue
            scope, phase = f[4:-4].split("_", 1)
            by_ts = defaultdict(float)
            mem_ts = defaultdict(float)
            for r in csv.DictReader(open(os.path.join(d, f))):
                if r["kind"] == "pod":
                    by_ts[r["ts"]] += num(r["cpu_m"]) or 0
                    mem_ts[r["ts"]] += num(r["mem_mi"]) or 0
            if by_ts:
                entries.append((v, scope, phase, statistics.mean(by_ts.values()), statistics.mean(mem_ts.values())))
    if not entries:
        return
    phases = [ph for ph in phases if any(e[2] == ph for e in entries)]
    series = sorted({(v, s) for v, s, _, _, _ in entries})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    width = 0.8 / max(len(series), 1)
    for ax, idx, ylabel, title in ((axes[0], 3, "millicores", "CPU of all pods in scope (mean)"),
                                   (axes[1], 4, "MiB", "memory of all pods in scope (mean)")):
        for i, (v, s) in enumerate(series):
            vals = []
            for ph in phases:
                m = [e[idx] for e in entries if e[0] == v and e[1] == s and e[2] == ph]
                vals.append(m[0] if m else 0)
            ax.bar([k + (i - (len(series) - 1) / 2) * width for k in range(len(phases))], vals, width * 0.9,
                   color=COLORS[i % len(COLORS)], alpha=0.8, label=f"{v}: {s}")
        ax.set_xticks(range(len(phases)))
        ax.set_xticklabels(phases)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(fontsize=8)
    save(fig, out, "resources.png")


def _span(path):
    ts = [int(r["ts"]) for r in csv.DictReader(open(path)) if r["ts"].isdigit()]
    return (min(ts), max(ts)) if ts else (None, None)


def _means(path, lo=None, hi=None):
    """mean over samples of: sum of pod rows, sum of node rows -> (pod_cpu, pod_mem, node_cpu, node_mem).
    Only rows with lo <= ts <= hi are used (the time both samplers were running)."""
    pod_c, pod_m, node_c, node_m = (defaultdict(float) for _ in range(4))
    for r in csv.DictReader(open(path)):
        if not r["ts"].isdigit() or (lo is not None and not lo <= int(r["ts"]) <= hi):
            continue
        c, m = num(r["cpu_m"]) or 0, num(r["mem_mi"]) or 0
        if r["kind"] == "pod":
            pod_c[r["ts"]] += c
            pod_m[r["ts"]] += m
        elif r["kind"] == "node":
            node_c[r["ts"]] += c
            node_m[r["ts"]] += m
    mean = lambda d: statistics.mean(d.values()) if d else 0
    return mean(pod_c), mean(pod_m), mean(node_c), mean(node_m)


def fig_layers(root, out, variants):
    """The 'three levels' figure, per variant that has both scopes:
       pods in the telco cluster -> the telco VMs as the guests see them -> virt-launcher pods -> whole workload node."""
    phases = ["idle", "ue", "iperf"]
    labels = ["pods in telco cluster", "telco VMs (guest view, node rows)", "virt-launcher pods (fiber view)", "whole workload node"]
    for v in variants:
        d = os.path.join(root, v, "res")
        data = {}
        for ph in phases:
            tf, ff = os.path.join(d, f"res_telco_{ph}.csv"), os.path.join(d, f"res_fiber_{ph}.csv")
            if os.path.exists(tf) and os.path.exists(ff):
                (a1, b1), (a2, b2) = _span(tf), _span(ff)
                if a1 is None or a2 is None:
                    continue
                lo, hi = max(a1, a2), min(b1, b2)
                if hi < lo:
                    print(f"skip {v}/{ph}: the telco and fiber samplers did not overlap in time (clocks or start times)")
                    continue
                print(f"{v}/{ph}: using the {hi - lo} s where both samplers ran")
                tp_c, tp_m, tn_c, tn_m = _means(tf, lo, hi)
                fp_c, fp_m, fn_c, fn_m = _means(ff, lo, hi)
                data[ph] = ([tp_c, tn_c, fp_c, fn_c], [tp_m, tn_m, fp_m, fn_m])
        if not data:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(10, 4.4))
        shown = [ph for ph in phases if ph in data]
        width = 0.8 / len(labels)
        for ax, idx, ylabel, title in ((axes[0], 0, "millicores", "CPU per layer (mean)"),
                                       (axes[1], 1, "MiB", "memory per layer (mean)")):
            for i, lab in enumerate(labels):
                vals = [data[ph][idx][i] for ph in shown]
                ax.bar([k + (i - (len(labels) - 1) / 2) * width for k in range(len(shown))], vals, width * 0.9,
                       color=COLORS[i % len(COLORS)], alpha=0.85, label=lab)
            ax.set_xticks(range(len(shown)))
            ax.set_xticklabels(shown)
            ax.set_ylabel(ylabel)
            ax.set_title(f"{title}: {v}")
            ax.grid(axis="y", alpha=0.3)
        axes[0].legend(fontsize=9, loc="upper center", bbox_to_anchor=(1.1, -0.1), ncol=2)   # below the axes, spanning both panels
        save(fig, out, f"resources_layers_{v}.png")


def fig_pr(root, out, variants):
    """PacketRusher control plane: per procedure the mean and max latency of a run against N (the report only holds
    aggregates per run), and the share of procedures that succeeded."""
    data = {v: read(root, v, "pr_runs.csv") for v in variants}
    if not any(data.values()):
        return
    fig = plt.figure(figsize=(11, 8.2))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1])
    axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, :])]
    procs = [("registration", "registration"), ("pdu_session_establishment", "PDU session set-up")]
    for ax, (proc, title) in zip(axes[:2], procs):
        for i, v in enumerate(variants):
            by = defaultdict(lambda: {"mean": [], "max": []})
            for r in data[v]:
                if r["proc"] != proc:
                    continue
                m, mx = num(r["lat_mean_ms"]), num(r["lat_max_ms"])
                if m is not None:
                    by[int(r["n"])]["mean"].append(m)
                if mx is not None:
                    by[int(r["n"])]["max"].append(mx)
            xs = sorted(x for x in by if by[x]["mean"])
            if not xs:
                continue
            c = COLORS[i % len(COLORS)]
            ax.plot(xs, [statistics.mean(by[x]["mean"]) for x in xs], "-o", color=c, label=f"{v}: mean")
            ax.plot(xs, [max(by[x]["max"]) for x in xs], ":^", color=c, label=f"{v}: max of the runs")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("number of UEs")
        ax.set_ylabel("latency (ms)")
        ax.set_title(f"PacketRusher: {title} latency")
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=7)
    ax = axes[2]
    for i, v in enumerate(variants):
        for j, (proc, title) in enumerate(procs):
            st, ok = defaultdict(float), defaultdict(float)
            for r in data[v]:
                if r["proc"] == proc and num(r["started"]) is not None:
                    st[int(r["n"])] += num(r["started"])
                    ok[int(r["n"])] += num(r["success"]) or 0
            xs = sorted(x for x in st if st[x] > 0)
            if xs:
                ax.plot(xs, [100 * ok[x] / st[x] for x in xs], "-o" if j == 0 else "--s",
                        color=COLORS[i % len(COLORS)], label=f"{v}: {title}")
    ax.set_xscale("log")
    lines = [y for ln in ax.get_lines() for y in ln.get_ydata()]
    lo = min(lines) if lines else 90
    ax.set_ylim(max(0, min(lo, 99) - 2), 100.5)      # zoomed: everything is close to 100%
    ax.axhline(100, color="gray", lw=0.6)
    ax.set_xlabel("number of UEs")
    ax.set_ylabel("successful (%), zoomed axis")
    ax.set_title("PacketRusher: success rate")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=7)
    save(fig, out, "pr_control_plane.png")


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    root, out, variants = os.path.expanduser(sys.argv[1]), sys.argv[2], sys.argv[3:]
    fig_ue(root, out, variants)
    fig_throughput(root, out, variants)
    fig_rtt(root, out, variants)
    fig_resources(root, out, variants)
    fig_layers(root, out, variants)
    fig_pr(root, out, variants)


if __name__ == "__main__":
    main()
