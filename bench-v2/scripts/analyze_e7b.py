#!/usr/bin/env python3
"""E7b: PacketRusher at 500 and 1000 phones without the sampler, after the NRF change, compared with E7.

usage: analyze_e7b.py <results root> <output dir>
reads : <root>/e7/<variant>/rep<k>/pr/pr_N0500/1000_r01.json            E7, 17 Oct, NRF 0.25
        <root>/e7b-nrf750/<variant>/rep<k>/pr/...                        19 Oct, NRF 0.75 (both variants)
        <root>/e7b-diag-nrf250/nested/rep<k>/pr/...                      19 Oct, nested only, NRF back to 0.25 (diagnostic)
writes: <out>/tables/e7b.tex, <out>/figs/e7b.png
"""
import json, os, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
SETS = [("e7", "E7, 17 Oct, NRF 0.25", ["nested", "direct"]),
        ("e7b-nrf750", "19 Oct, NRF 0.75", ["nested", "direct"]),
        ("e7b-diag-nrf250", "19 Oct, NRF 0.25 (diagnostic)", ["nested"])]
NS = [500, 1000]
PROCS = [("registration", "registration"), ("pdu_session_establishment", "PDU session")]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

data = defaultdict(list)     # (set, variant, n, proc) -> [mean ms per run]
fails = defaultdict(int)     # (set, variant, n) -> rejected registrations
for sdir, _, variants in SETS:
    for v in variants:
        for k in range(1, 6):
            for n in NS:
                f = os.path.join(ROOT, sdir, v, f"rep{k}", "pr", f"pr_N{n:03d}_r01.json")
                if not os.path.exists(f):
                    continue
                p = {x["procedure"]: x for x in json.load(open(f))["procedures"]}
                fails[(sdir, v, n)] += p["registration"]["failure"]
                for proc, _ in PROCS:
                    data[(sdir, v, n, proc)].append(p[proc]["latency_seconds_mean"] * 1000)


def fmt(vals):
    return f"{st.median(vals):.0f} ({min(vals):.0f}--{max(vals):.0f})" if vals else "--"


lines = [r"\begin{tabular}{l l r l l r}", r"\toprule",
         r"session & variant & phones & registration (ms) & PDU session (ms) & rejected \\", r"\midrule"]
for sdir, label, variants in SETS:
    for v in variants:
        for n in NS:
            reg, pdu = data[(sdir, v, n, "registration")], data[(sdir, v, n, "pdu_session_establishment")]
            if reg:
                lines.append(f"{label} & {v} & {n} & {fmt(reg)} & {fmt(pdu)} & {fails[(sdir, v, n)]}/{len(reg) * n}" + r" \\")
    lines.append(r"\midrule")
lines[-1] = r"\bottomrule"
with open(os.path.join(OUT, "tables", "e7b.tex"), "w") as f:
    f.write("\n".join(lines + [r"\end{tabular}"]) + "\n")

fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=False)
xs = {s: i for i, (s, _, _) in enumerate(SETS)}
for ax, (proc, plabel) in zip(axes, PROCS):
    for n, mk in zip(NS, ("o", "s")):
        for v in ("nested", "direct"):
            for sdir, _, variants in SETS:
                if v not in variants:
                    continue
                vals = data[(sdir, v, n, proc)]
                if not vals:
                    continue
                x = xs[sdir] + (-0.12 if v == "nested" else 0.12) + (-0.04 if n == 500 else 0.04)
                ax.scatter([x] * len(vals), vals, color=COLOR[v], marker=mk, alpha=0.5, s=25)
                ax.scatter([x], [st.median(vals)], color=COLOR[v], marker=mk, s=110, edgecolor="black",
                           label=f"{v}, {n} phones" if sdir == "e7b-nrf750" else None)
    ax.set_xticks(range(len(SETS)), [l for _, l, _ in SETS], fontsize=10)
    ax.set_ylabel("mean per run (ms)")
    ax.set_title(f"PacketRusher: {plabel}")
    ax.grid(axis="y", alpha=0.3)
axes[0].legend(fontsize=9, loc="upper left")
fig.suptitle("Small dots: runs; large dots: median of the runs")
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "e7b.png"), dpi=150)
for k in sorted(data):
    print(k, [round(x) for x in data[k]])
print(dict(fails))
