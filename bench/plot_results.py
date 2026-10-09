#!/usr/bin/env python3
"""Draw the thesis figures from the CSVs written by parse_results.py.

usage: plot_results.py <results_root> <out_dir> <variant> [<variant> ...]
       plot_results.py ~/bench-results figs nested direct

Needs matplotlib (pip install matplotlib). Missing inputs are skipped, so you can plot with only one variant.
Figures: ue_latency.png  ue_total.png  throughput.png  rtt.png  resources.png
"""
import csv, os, statistics, sys
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLORS = ["#1f77b4", "#d95f02", "#2ca02c", "#7570b3", "#e7298a"]
PATH_LABEL = {"pod": "pod-to-pod", "ue": "UE via UPF"}


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
    fig.tight_layout()
    fig.savefig(p, dpi=200)
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
    series = [(v, p) for v in variants for p in ("pod", "ue")
              if any(r["path"] == p for r in rows[v])]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    # TCP bars
    tags = ["P1", "P4"]
    width = 0.8 / max(len(series), 1)
    for i, (v, p) in enumerate(series):
        means, errs = [], []
        for t in tags:
            vals = [num(r["mbps"]) for r in rows[v] if r["path"] == p and r["proto"] == "tcp" and r["tag"] == t]
            vals = [x for x in vals if x is not None]
            means.append(statistics.mean(vals) if vals else 0)
            errs.append(statistics.pstdev(vals) if len(vals) > 1 else 0)
        axes[0].bar([k + (i - (len(series) - 1) / 2) * width for k in range(len(tags))], means, width * 0.9,
                    yerr=errs, capsize=3, color=COLORS[i % len(COLORS)], alpha=0.8, label=f"{v}: {PATH_LABEL[p]}")
    axes[0].set_xticks(range(len(tags)))
    axes[0].set_xticklabels(["TCP, 1 stream", "TCP, 4 streams"])
    axes[0].set_ylabel("Mbit/s")
    axes[0].set_title("TCP throughput (mean, std)")
    axes[0].legend(fontsize=7)
    axes[0].grid(axis="y", alpha=0.3)

    # UDP achieved and loss
    offered = sorted({int(r["tag"][1:-1]) for v in variants for r in rows[v] if r["proto"] == "udp"})
    for i, (v, p) in enumerate(series):
        ach, loss = [], []
        for o in offered:
            sel = [r for r in rows[v] if r["path"] == p and r["proto"] == "udp" and r["tag"] == f"b{o}M"]
            a = [num(r["mbps"]) for r in sel if num(r["mbps"]) is not None]
            l = [num(r["loss_pct"]) for r in sel if num(r["loss_pct"]) is not None]
            ach.append(statistics.mean(a) if a else float("nan"))
            loss.append(statistics.mean(l) if l else float("nan"))
        c = COLORS[i % len(COLORS)]
        axes[1].plot(offered, ach, marker="o", color=c, label=f"{v}: {PATH_LABEL[p]}")
        axes[2].plot(offered, loss, marker="o", color=c, label=f"{v}: {PATH_LABEL[p]}")
    if offered:
        axes[1].plot(offered, offered, "k--", lw=0.8, label="ideal")
    axes[1].set_xlabel("offered load (Mbit/s)")
    axes[1].set_ylabel("achieved (Mbit/s)")
    axes[1].set_title("UDP achieved throughput")
    axes[1].legend(fontsize=7)
    axes[2].set_xlabel("offered load (Mbit/s)")
    axes[2].set_ylabel("packet loss (%)")
    axes[2].set_title("UDP packet loss")
    for a in axes[1:]:
        a.grid(alpha=0.3)
    save(fig, out, "throughput.png")


def fig_rtt(root, out, variants):
    labels, data = [], []
    for v in variants:
        rows = read(root, v, "ping.csv")
        for p in ("pod", "ue"):
            vals = [num(r["rtt_ms"]) for r in rows if r["path"] == p]
            vals = [x for x in vals if x is not None]
            if vals:
                labels.append(f"{v}\n{PATH_LABEL[p]}")
                data.append(vals)
    if not data:
        return
    fig, ax = plt.subplots(figsize=(1.6 * len(data) + 2, 4))
    bp = ax.boxplot(data, patch_artist=True, showfliers=False)
    ax.set_xticks(range(1, len(data) + 1))
    ax.set_xticklabels(labels)
    for i, box in enumerate(bp["boxes"]):
        box.set(facecolor=COLORS[(i // 2) % len(COLORS)], alpha=0.6)
    ax.set_ylabel("RTT (ms)")
    ax.set_title("Round-trip time to the data-network server")
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


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    root, out, variants = os.path.expanduser(sys.argv[1]), sys.argv[2], sys.argv[3:]
    fig_ue(root, out, variants)
    fig_throughput(root, out, variants)
    fig_rtt(root, out, variants)
    fig_resources(root, out, variants)


if __name__ == "__main__":
    main()
