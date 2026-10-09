#!/usr/bin/env python3
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
Standard library only.
"""
import csv, glob, json, os, re, sys
from datetime import datetime

LINE = re.compile(r"^\[(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\] \[(?P<imsi>\d{15,16})\|nas\] \[\w+\] (?P<msg>.*)$")
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
                ev.setdefault(m["imsi"], {}).setdefault(key, ts(m["ts"]))
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
        m = re.match(r"iperf_(pod|ue)_(tcp|udp)_(.+)\.json$", os.path.basename(path))
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


if __name__ == "__main__":
    main()
