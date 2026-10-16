#!/usr/bin/env python3
# bench-v2 copy of bench/parse_results.py (pilot). Usage comments still name the pilot's folders; pass OUT=~/bench-v2-results/... instead.
"""Turn the raw benchmark outputs of one variant into CSV files.

usage: parse_results.py <results/variant>          e.g. parse_results.py ~/bench-results/nested

reads : ue/ue_N<n>_r<run>.log          UERANSIM UE logs        (run_ue_scaling.sh)
        dp/iperf_<path>_<proto>_<tag>.json   concatenated iperf3 -J outputs  (run_dataplane.sh)
        dp/ping_<path>.txt             ping output
writes: ue_times.csv   one row per UE:   n,run,imsi,reg_ms,pdu_ms,ready_ms
        ue_runs.csv    one row per run:  n,run,registered,sessions,total_ms
        iperf.csv      one row per test: path,proto,tag,run,mbps,loss_pct,jitter_ms,retrans
        ping.csv       one row per echo: path,seq,rtt_ms
        ping_summary.csv  path,sent_loss_pct
        pr_runs.csv    PacketRusher: one row per run and procedure: n,run,proc,started,success,failure,pending,
                       lat_min_ms,lat_mean_ms,lat_max_ms   (from pr/pr_N<n>_r<run>.json; see BENCHMARKS.md section 16)
        pr_report_flat.csv  every numeric field of those reports (n,run,key,value), to check the field names
Standard library only.
"""
import csv, glob, json, os, re, sys
from datetime import datetime

# With several UEs every line is prefixed [<imsi>|nas]; with a single UE (N=1) UERANSIM prints plain [nas].
LINE = re.compile(r"^\[(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\] \[(?:(?P<imsi>\d{15,16})\|)?nas\] \[\w+\] (?P<msg>.*)$")
EVENTS = {
    "reg_start": "Sending Initial Registration",
    "reg_ok": "Initial Registration is successful",
    "pdu_start": "Sending PDU Session Establishment Request",
    "pdu_ok": "PDU Session establishment is successful",
}


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").timestamp()


def parse_ue_log(path):
    ev = {}
    for line in open(path, errors="replace"):
        m = LINE.match(line.strip())
        if not m:
            continue
        for key, text in EVENTS.items():
            if m["msg"].startswith(text):
                ev.setdefault(m["imsi"] or "single-ue", {}).setdefault(key, ts(m["ts"]))
    return ev


def ue_logs(root):
    times, runs = [], []
    for path in sorted(glob.glob(os.path.join(root, "ue", "ue_N*_r*.log"))):
        m = re.search(r"ue_N(\d+)_r(\d+)\.log$", path)
        n, run = int(m[1]), int(m[2])
        ev = parse_ue_log(path)
        starts = [e["reg_start"] for e in ev.values() if "reg_start" in e]
        t0 = min(starts) if starts else None
        ready = []
        for imsi, e in sorted(ev.items()):
            row = {"n": n, "run": run, "imsi": imsi, "reg_ms": "", "pdu_ms": "", "ready_ms": ""}
            if "reg_start" in e and "reg_ok" in e:
                row["reg_ms"] = round((e["reg_ok"] - e["reg_start"]) * 1000, 1)
            if "pdu_start" in e and "pdu_ok" in e:
                row["pdu_ms"] = round((e["pdu_ok"] - e["pdu_start"]) * 1000, 1)
            if "pdu_ok" in e and t0 is not None:
                row["ready_ms"] = round((e["pdu_ok"] - t0) * 1000, 1)
                ready.append(row["ready_ms"])
            times.append(row)
        runs.append({
            "n": n, "run": run,
            "registered": sum(1 for e in ev.values() if "reg_ok" in e),
            "sessions": sum(1 for e in ev.values() if "pdu_ok" in e),
            "total_ms": max(ready) if ready else "",
        })
    return times, runs


def iperf_files(root):
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "dp", "iperf_*.json"))):
        m = re.match(r"iperf_(pod|ue|pr)_(tcp|udp)_(.+)\.json$", os.path.basename(path))
        if not m:
            continue
        pth, proto, tag = m.groups()
        text, pos, run = open(path, errors="replace").read(), 0, 0
        dec = json.JSONDecoder()
        while True:
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text):
                break
            if text[pos] != "{":          # stray text between objects (e.g. a "tun ip:" line): skip to the next JSON object
                nxt = text.find("\n{", pos)
                if nxt < 0:
                    break
                pos = nxt + 1
                continue
            try:
                obj, pos = dec.raw_decode(text, pos)
            except ValueError:
                break
            run += 1
            if "error" in obj or "end" not in obj:
                continue
            end = obj["end"]
            if proto == "tcp":
                s = end.get("sum_received") or end.get("sum") or {}
                rows.append({"path": pth, "proto": proto, "tag": tag, "run": run,
                             "mbps": round(s.get("bits_per_second", 0) / 1e6, 2), "loss_pct": "", "jitter_ms": "",
                             "retrans": (end.get("sum_sent") or {}).get("retransmits", "")})
            else:
                s = end.get("sum") or {}
                rows.append({"path": pth, "proto": proto, "tag": tag, "run": run,
                             "mbps": round(s.get("bits_per_second", 0) / 1e6, 2),
                             "loss_pct": round(s.get("lost_percent", 0), 3),
                             "jitter_ms": round(s.get("jitter_ms", 0), 3), "retrans": ""})
    return rows


def ping_files(root):
    rows, summ = [], []
    for path in sorted(glob.glob(os.path.join(root, "dp", "ping_*.txt"))):
        pth = re.match(r"ping_(.+)\.txt$", os.path.basename(path))[1]
        text = open(path, errors="replace").read()
        for m in re.finditer(r"icmp_seq=(\d+).*?time=([\d.]+) ms", text):
            rows.append({"path": pth, "seq": int(m[1]), "rtt_ms": float(m[2])})
        loss = re.search(r"([\d.]+)% packet loss", text)
        summ.append({"path": pth, "loss_pct": float(loss[1]) if loss else ""})
    return rows, summ


def _flatten(obj, prefix=""):
    """{"a": {"b": 1}} -> {"a_b": 1}; keeps numbers only."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}_"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            name = v.get("procedure", i) if isinstance(v, dict) else i
            out.update(_flatten(v, f"{prefix}{name}_"))
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out[prefix[:-1]] = obj
    return out


def _latency_ms(flat):
    """Pick min / mean / max latency out of a flattened procedure dict, whatever the exact key names are.
    Keys are matched by words (min, max, mean/avg, sum, count); a key with 'ms' or 'milli' is taken as milliseconds,
    anything else as seconds (PacketRusher's report uses latency_seconds_*)."""
    got = {}
    for k, v in flat.items():
        kl = k.lower()
        if "latenc" not in kl and "duration" not in kl:
            continue
        unit = 1.0 if ("_ms" in kl or "milli" in kl) else 1000.0
        for word, name in (("min", "min"), ("max", "max"), ("mean", "mean"), ("avg", "mean"), ("average", "mean")):
            if word in kl and name not in got:
                got[name] = v * unit
        if "sum" in kl:
            got["sum"] = v * unit
        if "count" in kl:
            got["count"] = v
    if "mean" not in got and got.get("count"):
        got["mean"] = got.get("sum", 0) / got["count"]
    return got.get("min", ""), got.get("mean", ""), got.get("max", "")


def pr_reports(root):
    rows, flat_rows = [], []
    for path in sorted(glob.glob(os.path.join(root, "pr", "pr_N*_r*.json"))):
        m = re.search(r"pr_N(\d+)_r(\d+)\.json$", path)
        n, run = int(m[1]), int(m[2])
        try:
            data = json.load(open(path))
        except ValueError:
            print(f"skipping {path}: not valid JSON")
            continue
        for k, v in _flatten(data).items():
            flat_rows.append({"n": n, "run": run, "key": k, "value": v})
        procs = data.get("procedures") if isinstance(data, dict) else None
        # the real report (v2.0.0) is a LIST of {"procedure": "<name>", "started": ..., ...}; accept a dict keyed by name too
        if isinstance(procs, list):
            items = [(b.get("procedure", f"procedure{i}"), b) for i, b in enumerate(procs) if isinstance(b, dict)]
        elif isinstance(procs, dict):
            items = list(procs.items())
        else:
            print(f"{path}: no 'procedures' in the report; see pr_report_flat.csv for what it contains")
            continue
        for name, body in items:
            flat = _flatten(body)
            lo, mean, hi = _latency_ms(flat)
            rows.append({"n": n, "run": run, "proc": name,
                         "started": flat.get("started", ""), "success": flat.get("success", ""),
                         "failure": flat.get("failure", ""), "pending": flat.get("pending", ""),
                         "lat_min_ms": round(lo, 2) if lo != "" else "",
                         "lat_mean_ms": round(mean, 2) if mean != "" else "",
                         "lat_max_ms": round(hi, 2) if hi != "" else ""})
    return rows, flat_rows


def write(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"{path}: {len(rows)} rows")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = os.path.expanduser(sys.argv[1])
    times, runs = ue_logs(root)
    write(os.path.join(root, "ue_times.csv"), times, ["n", "run", "imsi", "reg_ms", "pdu_ms", "ready_ms"])
    write(os.path.join(root, "ue_runs.csv"), runs, ["n", "run", "registered", "sessions", "total_ms"])
    write(os.path.join(root, "iperf.csv"), iperf_files(root),
          ["path", "proto", "tag", "run", "mbps", "loss_pct", "jitter_ms", "retrans"])
    prows, psumm = ping_files(root)
    write(os.path.join(root, "ping.csv"), prows, ["path", "seq", "rtt_ms"])
    write(os.path.join(root, "ping_summary.csv"), psumm, ["path", "loss_pct"])
    prr, prf = pr_reports(root)
    write(os.path.join(root, "pr_runs.csv"), prr,
          ["n", "run", "proc", "started", "success", "failure", "pending", "lat_min_ms", "lat_mean_ms", "lat_max_ms"])
    write(os.path.join(root, "pr_report_flat.csv"), prf, ["n", "run", "key", "value"])


if __name__ == "__main__":
    main()
