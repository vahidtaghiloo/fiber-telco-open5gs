#!/usr/bin/env python3
"""E1: control-plane capacity ramp (run_ramp.sh), nested vs direct.

usage: analyze_e1.py <e1 results dir> <output dir>
  e.g. analyze_e1.py ~/Desktop/thesis/bench-v2-results/e1 ~/Desktop/thesis/bench-v2-report/e1

reads : <e1>/<variant>/rep<k>/tr<TR>/pr_N<N>_r01.json   (PacketRusher reports)
writes: <out>/tables/e1_steps.csv, e1_steps.tex, e1_capacity.tex, <out>/figs/e1_ramp.png

Service level (fixed before the measurement): a step is met when at least 99 % of the phones registered and the mean
registration time of the run is at most 1 s. PacketRusher reports means per run, not percentiles, so the mean is used.
Capacity of a repetition = the highest offered rate whose step is met.
"""
import csv, json, os, re, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

E1, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
SLO_SHARE, SLO_MEAN_MS = 99.0, 1000.0
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

steps = defaultdict(dict)        # (variant, rep) -> {rate: row}
for v in V:
    vdir = os.path.join(E1, v)
    if not os.path.isdir(vdir):
        continue
    for rep in sorted(os.listdir(vdir)):
        for trdir in sorted(os.listdir(os.path.join(vdir, rep))) if os.path.isdir(os.path.join(vdir, rep)) else []:
            m = re.fullmatch(r"tr(\d+)", trdir)
            if not m:
                continue
            tr = int(m.group(1))
            files = [f for f in os.listdir(os.path.join(vdir, rep, trdir)) if re.fullmatch(r"pr_N\d+_r01\.json", f)]
            if not files:
                continue
            n = int(re.search(r"pr_N(\d+)_", files[0]).group(1))
            p = {x["procedure"]: x for x in json.load(open(os.path.join(vdir, rep, trdir, files[0])))["procedures"]}
            reg, pdu = p["registration"], p["pdu_session_establishment"]
            share = reg["success"] / n * 100
            row = {"rate": 1000 / tr, "tr": tr, "n": n, "ok": reg["success"], "rejected": reg["failure"], "pending": reg["pending"],
                   "share": share, "reg_mean": reg["latency_seconds_mean"] * 1000, "reg_max": reg["latency_seconds_max"] * 1000,
                   "pdu_mean": pdu["latency_seconds_mean"] * 1000,
                   "met": share >= SLO_SHARE and reg["latency_seconds_mean"] * 1000 <= SLO_MEAN_MS}
            steps[(v, rep)][1000 / tr] = row

with open(os.path.join(OUT, "tables", "e1_steps.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["variant", "rep", "offered_per_s", "tr_ms", "phones", "registered", "rejected", "pending", "registered_pct",
                "reg_mean_ms", "reg_max_ms", "pdu_mean_ms", "service_level_met"])
    for (v, rep), rows in sorted(steps.items()):
        for rate, r in sorted(rows.items()):
            w.writerow([v, rep, round(rate, 1), r["tr"], r["n"], r["ok"], r["rejected"], r["pending"], round(r["share"], 1),
                        round(r["reg_mean"]), round(r["reg_max"]), round(r["pdu_mean"]), r["met"]])

rates = sorted({rate for rows in steps.values() for rate in rows})


def agg(v, rate, key):
    return [rows[rate][key] for (vv, _), rows in steps.items() if vv == v and rate in rows]


def fmt(vals, f="{:.0f}"):
    if not vals:
        return "--"
    return f.format(st.median(vals)) + " (" + f.format(min(vals)) + "--" + f.format(max(vals)) + ")"


lines = [r"\begin{tabular}{r r ll ll rr}", r"\toprule",
         r" & & \multicolumn{2}{c}{registration, mean per run (ms)} & \multicolumn{2}{c}{registered (\%)} & \multicolumn{2}{c}{rejected (sum)} \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(lr){7-8}",
         r"offered / s & phones & nested & direct & nested & direct & nested & direct \\", r"\midrule"]
for rate in rates:
    n = agg("nested", rate, "n") or agg("direct", rate, "n")
    lines.append(f"{rate:.0f} & {n[0]} & {fmt(agg('nested', rate, 'reg_mean'))} & {fmt(agg('direct', rate, 'reg_mean'))} & "
                 f"{fmt(agg('nested', rate, 'share'), '{:.1f}')} & {fmt(agg('direct', rate, 'share'), '{:.1f}')} & "
                 f"{sum(agg('nested', rate, 'rejected'))} & {sum(agg('direct', rate, 'rejected'))}" + r" \\")
with open(os.path.join(OUT, "tables", "e1_steps.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

cap = {}
lines = [r"\begin{tabular}{l l r}", r"\toprule", r"variant & repetition & capacity (phones/s) \\", r"\midrule"]
for (v, rep), rows in sorted(steps.items()):
    met = [rate for rate, r in rows.items() if r["met"]]
    cap[(v, rep)] = max(met) if met else 0
    lines.append(f"{v} & {rep.replace('rep', '')} & " + (f"{cap[(v, rep)]:.0f}" if met else "below the lowest step") + r" \\")
with open(os.path.join(OUT, "tables", "e1_capacity.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

fig, axes = plt.subplots(1, 2, figsize=(13, 5))
for v in V:
    for (vv, rep), rows in steps.items():
        if vv != v:
            continue
        xs = sorted(rows)
        axes[0].plot(xs, [rows[x]["reg_mean"] for x in xs], color=COLOR[v], alpha=0.35, lw=1, marker=".")
        axes[1].plot(xs, [rows[x]["share"] for x in xs], color=COLOR[v], alpha=0.35, lw=1, marker=".")
    med = [st.median(agg(v, r, "reg_mean")) if agg(v, r, "reg_mean") else float("nan") for r in rates]
    shr = [st.median(agg(v, r, "share")) if agg(v, r, "share") else float("nan") for r in rates]
    axes[0].plot(rates, med, color=COLOR[v], lw=2.5, marker="o", label=f"{v} (median; thin: single repetitions)")
    axes[1].plot(rates, shr, color=COLOR[v], lw=2.5, marker="o", label=v)
axes[0].axhline(SLO_MEAN_MS, color="green", ls="--", lw=1, label="service level: mean 1 s")
axes[1].axhline(SLO_SHARE, color="green", ls="--", lw=1, label="service level: 99 %")
for ax in axes:
    ax.set_xscale("log"); ax.set_xticks(rates, [f"{r:.0f}" for r in rates]); ax.set_xlabel("offered rate, phones per second (log)")
    ax.grid(alpha=0.3, which="both")
axes[0].set_yscale("log"); axes[0].set_ylabel("mean registration time per run, ms (log)"); axes[0].set_title("Registration time against load")
axes[1].set_ylabel("registered within the run (%)"); axes[1].set_title("Share registered against load")
lo = min([r["share"] for rows in steps.values() for r in rows.values()] + [95])
axes[1].set_ylim(max(0, lo - 2), 100.5)
axes[0].legend(fontsize=9, loc="upper left"); axes[1].legend(fontsize=9, loc="lower left")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "e1_ramp.png"), dpi=150)
for k, c in sorted(cap.items()):
    print("capacity", k, round(c, 1))
