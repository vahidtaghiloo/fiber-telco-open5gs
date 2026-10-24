#!/usr/bin/env python3
"""E3: one control-plane CPU limit lowered at a time (run_e3.sh), nested vs direct, 1000 phones at 50 per second.

usage: analyze_e3.py <e3 results dir> <output dir>
  e.g. analyze_e3.py ~/Desktop/thesis/bench-v2-results/e3 ~/Desktop/thesis/bench-v2-report/e3

reads : <e3>/<variant>/<setting>/rep<k>/pr_N1000_r01.json and cadvisor_e3.csv
writes: <out>/tables/e3_runs.csv, e3_throttle.csv, e3_results.tex, e3_throttle.tex, <out>/figs/e3_limits.png and .pdf

Service level (F8, as in E1): at least 99 % of the phones registered and a mean registration time of at most 1 s.
Throttling: share of the CFS periods in which a container was throttled, over the window of the run (PacketRusher's
started_at/ended_at), from samples where the kubelet counter changed.
"""
import csv, datetime, glob, json, os, re, statistics as st, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

E3, OUT = (os.path.expanduser(a) for a in sys.argv[1:3])
V = ["nested", "direct"]
COLOR = {"nested": "#1f77b4", "direct": "#d95f02"}
ORDER = ["base", "amf-750m", "amf-500m", "amf-250m", "smf-750m", "smf-500m", "smf-250m",
         "mongodb-1000m", "mongodb-500m", "udm-250m"]
LABEL = {"base": "FIXED (baseline)", "amf-750m": "AMF 0.75", "amf-500m": "AMF 0.5", "amf-250m": "AMF 0.25",
         "smf-750m": "SMF 0.75", "smf-500m": "SMF 0.5", "smf-250m": "SMF 0.25",
         "mongodb-1000m": "MongoDB 1", "mongodb-500m": "MongoDB 0.5", "udm-250m": "UDM 0.25"}
CTR = {"amf": "open5gs-amf", "smf": "open5gs-smf", "mongodb": "mongodb", "udm": "open5gs-udm"}
WATCH = ["open5gs-amf", "open5gs-smf", "open5gs-udm", "open5gs-udr", "open5gs-ausf", "open5gs-nrf", "open5gs-scp",
         "open5gs-pcf", "mongodb", "packetrusher"]
SLO_SHARE, SLO_MEAN_MS = 99.0, 1000.0
for d in ("figs", "tables"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)


def ts(s):   # RFC 3339 with nanoseconds -> epoch seconds
    s = s.rstrip("Z"); main, _, frac = s.partition(".")
    return datetime.datetime.strptime(main, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp() + float("0." + (frac or "0"))


runs = []
thr = {}                                     # (variant, setting, rep, container) -> (cores, throttled %)
for v in V:
    for sdir in glob.glob(os.path.join(E3, v, "*")):
        setting = os.path.basename(sdir)
        if setting not in ORDER:
            continue
        for repdir in sorted(glob.glob(os.path.join(sdir, "rep*"))):
            rep = os.path.basename(repdir)
            f = os.path.join(repdir, "pr_N1000_r01.json")
            if not os.path.exists(f):
                print("missing report:", repdir); continue
            o = json.load(open(f))
            p = {x["procedure"]: x for x in o["procedures"]}
            reg, pdu = p["registration"], p.get("pdu_session_establishment", {})
            row = dict(variant=v, setting=setting, rep=rep, ok=reg["success"], rejected=reg["failure"], pending=reg["pending"],
                       reg_ms=reg["latency_seconds_mean"] * 1000 if reg["success"] else None,
                       pdu_ok=pdu.get("success", 0), pdu_ms=pdu["latency_seconds_mean"] * 1000 if pdu.get("success") else None)
            row["met"] = row["ok"] / 10 >= SLO_SHARE and row["reg_ms"] is not None and row["reg_ms"] <= SLO_MEAN_MS
            runs.append(row)
            t0, t1 = ts(o["started_at"]), ts(o["ended_at"])
            cf = os.path.join(repdir, "cadvisor_e3.csv")
            if not os.path.exists(cf):
                continue
            series = defaultdict(list)
            for r in csv.DictReader(open(cf)):
                try:
                    series[(r["container"], r["metric"])].append((float(r["ts"]), float(r["value"])))
                except (ValueError, KeyError):
                    continue
            for c in WATCH:
                def delta(metric):
                    pts = sorted(x for x in series.get((c, metric), []) if t0 - 3 <= x[0] <= t1 + 3)
                    pts = [q for i, q in enumerate(pts) if i == 0 or q[1] != pts[i - 1][1]]
                    return (pts[-1][1] - pts[0][1], pts[-1][0] - pts[0][0]) if len(pts) >= 2 else (None, None)
                used, dt = delta("container_cpu_usage_seconds_total")
                per, _ = delta("container_cpu_cfs_periods_total")
                th, _ = delta("container_cpu_cfs_throttled_periods_total")
                if used is not None and dt and per:
                    thr[(v, setting, rep, c)] = (used / dt, 100 * (th or 0) / per)

with open(os.path.join(OUT, "tables", "e3_runs.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["variant", "setting", "rep", "registered", "rejected", "pending", "reg_mean_ms", "pdu_ok", "pdu_mean_ms", "service_level_met"])
    for r in sorted(runs, key=lambda r: (r["variant"], ORDER.index(r["setting"]), r["rep"])):
        w.writerow([r["variant"], r["setting"], r["rep"], r["ok"], r["rejected"], r["pending"],
                    "" if r["reg_ms"] is None else round(r["reg_ms"]), r["pdu_ok"], "" if r["pdu_ms"] is None else round(r["pdu_ms"]), r["met"]])
with open(os.path.join(OUT, "tables", "e3_throttle.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["variant", "setting", "rep", "container", "cores", "throttled_pct"])
    for k, (c, t) in sorted(thr.items(), key=lambda kv: (kv[0][0], ORDER.index(kv[0][1]), kv[0][2], kv[0][3])):
        w.writerow(list(k) + [round(c, 3), round(t, 1)])


def get(v, s, key):
    return [r[key] for r in runs if (r["variant"], r["setting"]) == (v, s) and r[key] is not None]


def fmt(xs, f="{:.0f}"):
    return "--" if not xs else f.format(st.median(xs)) + " (" + f.format(min(xs)) + "--" + f.format(max(xs)) + ")"


def cell(v, s, key):
    if get(v, s, "ok") and max(get(v, s, "ok")) == 0:
        return "not operational"
    return fmt(get(v, s, key))


def thr_of(v, s, c, i=1):
    return [x[i] for (vv, ss, _, cc), x in thr.items() if (vv, ss, cc) == (v, s, c)]


settings = [s for s in ORDER if any(r["setting"] == s for r in runs)]
lines = [r"\begin{tabular}{l ll ll c}", r"\toprule",
         r" & \multicolumn{2}{c}{registration, mean per run (ms)} & \multicolumn{2}{c}{PDU-session set-up, mean (ms)} & service level met \\",
         r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}", r"limit lowered (CPU) & nested & direct & nested & direct & nested / direct \\", r"\midrule"]
for s in settings:
    met = [f"{sum(get(v, s, 'met'))}/{len(get(v, s, 'met'))}" for v in V]
    lines.append(f"{LABEL[s]} & {cell('nested', s, 'reg_ms')} & {cell('direct', s, 'reg_ms')} & "
                 f"{cell('nested', s, 'pdu_ms')} & {cell('direct', s, 'pdu_ms')} & {met[0]} / {met[1]}" + r" \\")
with open(os.path.join(OUT, "tables", "e3_results.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

lines = [r"\begin{tabular}{l l ll ll}", r"\toprule",
         r" & & \multicolumn{2}{c}{throttled periods (\%)} & \multicolumn{2}{c}{CPU used (cores)} \\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}", r"limit lowered & container & nested & direct & nested & direct \\", r"\midrule"]
for s in settings:
    if s == "base":
        shown = ["open5gs-amf", "open5gs-udm", "mongodb"]
    else:
        shown = [CTR[s.split("-")[0]]]
    for c in shown:
        lines.append(f"{LABEL[s]} & {c.replace('open5gs-', '').upper() if c != 'mongodb' else 'MongoDB'} & "
                     f"{fmt(thr_of('nested', s, c))} & {fmt(thr_of('direct', s, c))} & "
                     f"{fmt(thr_of('nested', s, c, 0), '{:.2f}')} & {fmt(thr_of('direct', s, c, 0), '{:.2f}')}" + r" \\")
with open(os.path.join(OUT, "tables", "e3_throttle.tex"), "w") as f:
    f.write("\n".join(lines + [r"\bottomrule", r"\end{tabular}"]) + "\n")

fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
xs = list(range(len(settings)))
for i, v in enumerate(V):
    off = (i - 0.5) * 0.3
    for ax, key in zip(axes, ("reg_ms", "pdu_ms")):
        for x, s in zip(xs, settings):
            ys = get(v, s, key)
            if get(v, s, "ok") and max(get(v, s, "ok")) == 0:
                ax.text(x + off, 1.2e4, "×", color=COLOR[v], ha="center", va="center", fontsize=13)
                continue
            if ys:
                ax.bar(x + off, st.median(ys), width=0.3, color=COLOR[v], label=v if x == 0 else None)
                ax.plot([x + off] * len(ys), ys, "k.", ms=3)
for ax, title in zip(axes, ("Registration, mean per run", "PDU-session set-up, mean per run")):
    ax.set_yscale("log"); ax.set_ylim(50, 2e4)
    ax.set_xticks(xs, [LABEL[s] for s in settings], rotation=35, ha="right")
    ax.set_ylabel("ms (bar: median, dots: runs; ×: core not operational)"); ax.set_title(title); ax.grid(axis="y", alpha=0.3, which="both")
axes[0].axhline(SLO_MEAN_MS, color="green", ls="--", lw=1, label="service level 1 s")
axes[0].legend(fontsize=9)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "figs", "e3_limits.png"), dpi=150)
fig.savefig(os.path.join(OUT, "figs", "e3_limits.pdf"))
print(f"{len(runs)} runs, {len(thr)} throttling windows -> {OUT}")
