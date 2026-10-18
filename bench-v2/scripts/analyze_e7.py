#!/usr/bin/env python3
"""E7 analysis: the pilot's sizes under fair conditions, 5 interleaved repetitions per variant, plus the comparison with the pilot.

usage: analyze_e7.py <e7 results dir> <pilot results dir> <output dir>
  e.g. analyze_e7.py ~/Desktop/thesis/bench-v2-results/e7 ~/Desktop/thesis/bench-results ~/Desktop/thesis/bench-v2-report/e7

reads : <e7>/<variant>/rep<k>/ue_times.csv, ue_runs.csv, pr_runs.csv   (parse_results.py, run once per repetition)
        <pilot>/<variant>/ue_times.csv, ue_runs.csv, pr_runs.csv      (the pilot's CSVs)
writes: <out>/tables/*.csv and *.tex (no hand-typed numbers in the report), <out>/figs/*.png, <out>/tables/numbers.txt

Exclusion rule (automatic, listed in excluded_runs.csv): a UERANSIM run in which no phone registered at all never reached
the core (the phone found no cell), so it says nothing about the core and is left out. Matplotlib is the only dependency.
"""
import csv, os, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

E7, PILOT, OUT = (os.path.expanduser(a) for a in sys.argv[1:4])
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}     # the pilot report's colours
REPS = [1, 2, 3, 4, 5]
plt.rcParams.update({"font.size": 12, "axes.titlesize": 13, "legend.fontsize": 11})
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)
NUM = []


def rows(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def q(vals, p):
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def tex(name, lines):
    with open(os.path.join(OUT, "tables", name), "w") as f:
        f.write("\n".join(lines) + "\n")


def wcsv(name, header, data):
    with open(os.path.join(OUT, "tables", name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(data)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "figs", name), dpi=150)
    plt.close(fig)


# ============================================================== load E7
ue = defaultdict(lambda: defaultdict(list))        # ue[(v, n)][metric] -> per-phone values, pooled over reps
ue_sessions = defaultdict(lambda: [0, 0])          # (v, n) -> [sessions, requested]  (included runs only)
ue_runs_used = defaultdict(int)
excluded = []
pr = defaultdict(lambda: defaultdict(dict))        # pr[(v, n)][proc][rep] -> row
for v in V:
    for k in REPS:
        d = os.path.join(E7, v, f"rep{k}")
        runs = rows(os.path.join(d, "ue_runs.csv"))
        bad = {int(r["n"]) for r in runs if int(r["registered"]) == 0}
        for n in sorted(bad):
            excluded.append([v, k, "UERANSIM", n, "no phone registered: the phone found no cell after the gNB restart; the core was never reached"])
        for r in runs:
            n = int(r["n"])
            if n not in bad:
                ue_sessions[(v, n)][0] += int(r["sessions"])
                ue_sessions[(v, n)][1] += n
                ue_runs_used[(v, n)] += 1
        for r in rows(os.path.join(d, "ue_times.csv")):
            n = int(r["n"])
            if n in bad or r["reg_ms"] == "" or r["pdu_ms"] == "":
                continue
            reg, pdu = float(r["reg_ms"]), float(r["pdu_ms"])
            ue[(v, n)]["reg"].append(reg)
            ue[(v, n)]["pdu"].append(pdu)
            ue[(v, n)]["attach"].append(reg + pdu)
        for r in rows(os.path.join(d, "pr_runs.csv")):
            pr[(v, int(r["n"]))][r["proc"]][k] = r
wcsv("excluded_runs.csv", ["variant", "rep", "generator", "n", "reason"], excluded)
UE_N = sorted({n for (_, n) in ue})
PR_N = sorted({n for (_, n) in pr})
PROCS = [("registration", "registration"), ("pdu_session_establishment", "PDU session")]

# ============================================================== load the pilot
pue = defaultdict(lambda: defaultdict(list))
ppr = defaultdict(lambda: defaultdict(list))       # ppr[(v, n)][proc] -> list of rows
for v in V:
    for r in rows(os.path.join(PILOT, v, "ue_times.csv")):
        if r["reg_ms"] != "" and r["pdu_ms"] != "":
            reg, pdu = float(r["reg_ms"]), float(r["pdu_ms"])
            n = int(r["n"])
            pue[(v, n)]["reg"].append(reg); pue[(v, n)]["pdu"].append(pdu); pue[(v, n)]["attach"].append(reg + pdu)
    for r in rows(os.path.join(PILOT, v, "pr_runs.csv")):
        if r["lat_mean_ms"] != "":
            ppr[(v, int(r["n"]))][r["proc"]].append(r)


def pr_means(v, n, proc, src="e7"):
    if src == "e7":
        return [float(x["lat_mean_ms"]) for x in pr[(v, n)][proc].values() if x["lat_mean_ms"] != ""]
    return [float(x["lat_mean_ms"]) for x in ppr[(v, n)][proc]]


def pr_sum(v, n, proc, field):
    return sum(int(x[field]) for x in pr[(v, n)][proc].values())


# ============================================================== tables: UERANSIM
lines = [r"\begin{tabular}{r rrrr rrrr}", r"\toprule",
         r" & \multicolumn{4}{c}{nested} & \multicolumn{4}{c}{direct} \\", r"\cmidrule(lr){2-5}\cmidrule(lr){6-9}",
         r"phones & median & p90 & max & sessions & median & p90 & max & sessions \\", r"\midrule"]
for n in UE_N:
    cells = []
    for v in V:
        a = ue[(v, n)]["attach"]
        s, req = ue_sessions[(v, n)]
        cells += [f"{st.median(a):.0f}", f"{q(a, .9):.0f}", f"{max(a):.0f}", f"{s}/{req}"]
        NUM.append(f"ue attach {v} N={n}: median {st.median(a):.0f} p90 {q(a, .9):.0f} max {max(a):.0f} sessions {s}/{req} runs {ue_runs_used[(v, n)]}")
    lines.append(f"{n} & " + " & ".join(cells) + r" \\")
tex("ueransim.tex", lines + [r"\bottomrule", r"\end{tabular}"])

lines = [r"\begin{tabular}{r rrr rrr}", r"\toprule",
         r" & \multicolumn{3}{c}{registration (median, ms)} & \multicolumn{3}{c}{PDU session (median, ms)} \\",
         r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}",
         r"phones & nested & direct & direct/nested & nested & direct & direct/nested \\", r"\midrule"]
for n in UE_N:
    cells = []
    for m in ("reg", "pdu"):
        a, b = st.median(ue[("nested", n)][m]), st.median(ue[("direct", n)][m])
        cells += [f"{a:.0f}", f"{b:.0f}", f"{b / a:.2f}"]
    lines.append(f"{n} & " + " & ".join(cells) + r" \\")
tex("ueransim_parts.tex", lines + [r"\bottomrule", r"\end{tabular}"])

# ============================================================== tables: PacketRusher
for proc, fname in (("registration", "pr_reg.tex"), ("pdu_session_establishment", "pr_pdu.tex")):
    lines = [r"\begin{tabular}{r rrrr rrrr r}", r"\toprule",
             r" & \multicolumn{4}{c}{nested} & \multicolumn{4}{c}{direct} & \\", r"\cmidrule(lr){2-5}\cmidrule(lr){6-9}",
             r"phones & median & run means & worst & ok/started & median & run means & worst & ok/started & d/n \\", r"\midrule"]
    for n in PR_N:
        cells, med = [], {}
        for v in V:
            m = pr_means(v, n, proc)
            worst = max(float(x["lat_max_ms"]) for x in pr[(v, n)][proc].values() if x["lat_max_ms"] != "")
            med[v] = st.median(m)
            cells += [f"{med[v]:.0f}", f"{min(m):.0f}--{max(m):.0f}", f"{worst:.0f}",
                      f"{pr_sum(v, n, proc, 'success')}/{pr_sum(v, n, proc, 'started')}"]
            NUM.append(f"pr {proc} {v} N={n}: median {med[v]:.0f} range {min(m):.0f}-{max(m):.0f} worst {worst:.0f} "
                       f"ok {pr_sum(v, n, proc, 'success')}/{pr_sum(v, n, proc, 'started')} pending {pr_sum(v, n, proc, 'pending')}")
        lines.append(f"{n} & " + " & ".join(cells) + f" & {med['direct'] / med['nested']:.2f}" + r" \\")
    tex(fname, lines + [r"\bottomrule", r"\end{tabular}"])

# ============================================================== tables: pilot vs E7
def ratio(dct, n, key, kind):
    if kind == "ue":
        a, b = dct[("direct", n)][key], dct[("nested", n)][key]
    else:
        a = [float(x["lat_mean_ms"]) for x in dct[("direct", n)][key]]
        b = [float(x["lat_mean_ms"]) for x in dct[("nested", n)][key]]
    return st.median(a) / st.median(b) if a and b else float("nan")


cmp = []
for n in PR_N:
    for proc, label in PROCS:
        e7r = st.median(pr_means("direct", n, proc)) / st.median(pr_means("nested", n, proc))
        cmp.append(["PacketRusher", label, n, ratio(ppr, n, proc, "pr"), e7r])
for n in UE_N:
    for m, label in (("reg", "registration"), ("pdu", "PDU session")):
        e7r = st.median(ue[("direct", n)][m]) / st.median(ue[("nested", n)][m])
        cmp.append(["UERANSIM", label, n, ratio(pue, n, m, "ue"), e7r])
wcsv("ratio_pilot_vs_e7.csv", ["generator", "procedure", "n", "pilot_ratio", "e7_ratio"], [r[:3] + [round(r[3], 2), round(r[4], 2)] for r in cmp])
lines = [r"\begin{tabular}{llr rr}", r"\toprule", r"generator & procedure & phones & pilot & E7 \\", r"\midrule"]
for g, label, n, a, b in cmp:
    lines.append(f"{g} & {label} & {n} & {a:.2f} & {b:.2f}" + r" \\")
tex("ratio.tex", lines + [r"\bottomrule", r"\end{tabular}"])

lines = [r"\begin{tabular}{llr rrr rrr}", r"\toprule",
         r" & & & \multicolumn{3}{c}{nested (median, ms)} & \multicolumn{3}{c}{direct (median, ms)} \\",
         r"\cmidrule(lr){4-6}\cmidrule(lr){7-9}",
         r"generator & what & phones & pilot & E7 & E7/pilot & pilot & E7 & E7/pilot \\", r"\midrule"]
for n in PR_N:
    for proc, label in PROCS:
        cells = []
        for v in V:
            a = st.median(pr_means(v, n, proc, "pilot")); b = st.median(pr_means(v, n, proc))
            cells += [f"{a:.0f}", f"{b:.0f}", f"{b / a:.1f}"]
        lines.append(f"PacketRusher & {label} & {n} & " + " & ".join(cells) + r" \\")
for n in UE_N:
    cells = []
    for v in V:
        a = st.median(pue[(v, n)]["attach"]); b = st.median(ue[(v, n)]["attach"])
        cells += [f"{a:.0f}", f"{b:.0f}", f"{b / a:.1f}"]
    lines.append(f"UERANSIM & attach & {n} & " + " & ".join(cells) + r" \\")
tex("abs_pilot_vs_e7.tex", lines + [r"\bottomrule", r"\end{tabular}"])

# ============================================================== figures
# 1 UERANSIM box plots: registration | PDU session
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
for ax, m, title in ((axes[0], "reg", "Registration time per phone"), (axes[1], "pdu", "PDU-session set-up time per phone")):
    pos = list(range(len(UE_N)))
    for i, v in enumerate(V):
        bp = ax.boxplot([ue[(v, n)][m] for n in UE_N], positions=[p + (i - 0.5) * 0.36 for p in pos], widths=0.32,
                        patch_artist=True, showfliers=False, medianprops={"color": "black"})
        for b in bp["boxes"]:
            b.set(facecolor=COLOR[v], alpha=0.6)
        ax.plot([], [], color=COLOR[v], lw=8, alpha=0.6, label=v)
    ax.set_xticks(pos, [str(n) for n in UE_N]); ax.set_xlabel("phones started together")
    ax.set_ylabel("ms (log)"); ax.set_yscale("log"); ax.set_title(title); ax.grid(axis="y", alpha=0.3)
axes[0].legend(loc="upper left")
save(fig, "ue_latency.png")

# 2 UERANSIM attach percentiles (median, p90, max)
fig, ax = plt.subplots(figsize=(8, 5))
for v in V:
    for stat, ls, lab in ((lambda a: st.median(a), "-", "median"), (lambda a: q(a, .9), "--", "p90"), (max, ":", "worst phone")):
        ax.plot(UE_N, [stat(ue[(v, n)]["attach"]) for n in UE_N], ls=ls, marker="o", color=COLOR[v], label=f"{v}, {lab}")
ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xticks(UE_N, [str(n) for n in UE_N])
ax.set_xlabel("phones started together (log)"); ax.set_ylabel("attach latency per phone, ms (log)")
ax.set_title("UERANSIM: registration + PDU session per phone"); ax.grid(alpha=0.3, which="both")
ax.legend(fontsize=9, ncol=2, loc="upper left")
save(fig, "ue_attach_percentiles.png")

# 3 PacketRusher control plane: mean (median of run means, min-max bars) and worst; completion
fig = plt.figure(figsize=(13, 9))
gs = fig.add_gridspec(2, 2, height_ratios=[1.3, 1])
for col, (proc, label) in enumerate(PROCS):
    ax = fig.add_subplot(gs[0, col])
    for i, v in enumerate(V):
        x = [n * (0.93 if v == "nested" else 1.07) for n in PR_N]
        med = [st.median(pr_means(v, n, proc)) for n in PR_N]
        lo = [m - min(pr_means(v, n, proc)) for m, n in zip(med, PR_N)]
        hi = [max(pr_means(v, n, proc)) - m for m, n in zip(med, PR_N)]
        ax.errorbar(x, med, yerr=[lo, hi], marker="o", capsize=3, color=COLOR[v], label=f"{v}, mean (median and range of 5 runs)")
        worst = [max(float(r["lat_max_ms"]) for r in pr[(v, n)][proc].values()) for n in PR_N]
        ax.plot(x, worst, ls=":", marker="^", color=COLOR[v], label=f"{v}, worst phone")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xticks(PR_N, [str(n) for n in PR_N])
    ax.set_xlabel("phones, one every 10 ms (log)"); ax.set_ylabel("ms (log)"); ax.set_title(f"PacketRusher: {label}")
    ax.grid(alpha=0.3, which="both")
    if col == 0:
        ax.legend(fontsize=9, loc="upper left")
ax = fig.add_subplot(gs[1, :])
w = 0.38
for i, v in enumerate(V):
    share = [pr_sum(v, n, "registration", "success") / pr_sum(v, n, "registration", "started") * 100 for n in PR_N]
    ax.bar([j + (i - 0.5) * w for j in range(len(PR_N))], share, width=w, color=COLOR[v], alpha=0.8, label=v)
    for j, s in enumerate(share):
        ax.text(j + (i - 0.5) * w, s + 0.05, f"{s:.1f}", ha="center", va="bottom", fontsize=9)
ax.set_xticks(range(len(PR_N)), [str(n) for n in PR_N]); ax.set_xlabel("phones")
ax.set_ylim(98, 100.6); ax.set_ylabel("registered within the run, % (zoomed)")
ax.set_title("Share of registrations completed, all 5 runs together"); ax.grid(axis="y", alpha=0.3); ax.legend(loc="lower left")
save(fig, "pr_control_plane.png")

# 4 completion at 1000 per repetition
n = max(PR_N)
fig, ax = plt.subplots(figsize=(8, 4.5))
for i, v in enumerate(V):
    vals = [int(pr[(v, n)]["registration"][k]["success"]) / int(pr[(v, n)]["registration"][k]["started"]) * 100 for k in REPS]
    ax.bar([k + (i - 0.5) * w for k in REPS], vals, width=w, color=COLOR[v], alpha=0.8, label=v)
ax.set_ylim(90, 100.5); ax.set_xticks(REPS, [f"rep {k}" for k in REPS]); ax.set_ylabel("registered within the run (%)")
ax.set_title(f"PacketRusher, {n} phones, per repetition"); ax.grid(axis="y", alpha=0.3)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2, frameon=False)
save(fig, "pr_completion_1000.png")

# 5 ratio direct/nested, pilot vs E7
fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
for ax, gen, ns in ((axes[0], "PacketRusher", PR_N), (axes[1], "UERANSIM", UE_N)):
    for label, mk in (("registration", "o"), ("PDU session", "s")):
        sel = [r for r in cmp if r[0] == gen and r[1] == label]
        ax.plot([r[2] for r in sel], [r[3] for r in sel], ls="--", marker=mk, color="grey", label=f"pilot, {label}")
        ax.plot([r[2] for r in sel], [r[4] for r in sel], ls="-", marker=mk, color="black", label=f"E7, {label}")
    ax.axhline(1, color="green", lw=1)
    ax.set_xscale("log"); ax.set_xticks(ns, [str(x) for x in ns]); ax.set_xlabel("phones (log)"); ax.set_title(gen); ax.grid(alpha=0.3)
axes[0].set_ylabel("median direct / median nested\n(above 1: direct slower)")
axes[1].legend(loc="upper left", fontsize=10)
save(fig, "ratio_pilot_vs_e7.png")

# 6 absolute values, pilot vs E7 (PacketRusher mean per run)
fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
for ax, (proc, label) in zip(axes, PROCS):
    for v in V:
        ax.plot(PR_N, [st.median(pr_means(v, n, proc, "pilot")) for n in PR_N], ls="--", marker="o", color=COLOR[v], alpha=0.6, label=f"{v}, pilot")
        ax.plot(PR_N, [st.median(pr_means(v, n, proc)) for n in PR_N], ls="-", marker="o", color=COLOR[v], label=f"{v}, E7")
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xticks(PR_N, [str(x) for x in PR_N])
    ax.set_xlabel("phones, one every 10 ms (log)"); ax.set_title(f"PacketRusher {label}: median of run means"); ax.grid(alpha=0.3, which="both")
axes[0].set_ylabel("ms (log)"); axes[0].legend(fontsize=10, loc="upper left")
save(fig, "abs_pilot_vs_e7.png")

with open(os.path.join(OUT, "tables", "numbers.txt"), "w") as f:
    f.write("\n".join(NUM) + "\n")
print("\n".join(NUM))
print("excluded:", excluded)
