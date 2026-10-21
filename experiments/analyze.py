#!/usr/bin/env python3
"""Compute latency / packet-loss metrics from one run directory (sent.csv + recv_*.csv)."""
import csv, glob, math, os, sys


def _pct(v, p):
    if not v:
        return float("nan")
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def analyze(run_dir):
    expected, retx = {}, 0
    with open(os.path.join(run_dir, "sent.csv")) as f:
        for r in csv.DictReader(f):
            if r["attempt"] == "0":
                expected[(int(r["seq"]), r["sub"])] = float(r["ts"])
            else:
                retx += 1
    first, first0 = {}, {}
    for fn in glob.glob(os.path.join(run_dir, "recv_*.csv")):
        with open(fn) as f:
            for r in csv.DictReader(f):
                k, t = (int(r["seq"]), r["sub"]), float(r["recv_ts"])
                if k not in first or t < first[k]:
                    first[k] = t
                if r["attempt"] == "0" and (k not in first0 or t < first0[k]):
                    first0[k] = t
    n = len(expected)
    e2e = [(first[k] - ts) * 1000 for k, ts in expected.items() if k in first]
    net = [(first0[k] - ts) * 1000 for k, ts in expected.items() if k in first0]
    nan = float("nan")
    return {
        "sent": n,
        "delivered_pct": round(100.0 * len(e2e) / n, 2) if n else nan,
        "first_try_loss_pct": round(100.0 * (1 - len(net) / n), 2) if n else nan,
        "residual_loss_pct": round(100.0 * (1 - len(e2e) / n), 2) if n else nan,
        "net_lat_avg_ms": round(sum(net) / len(net), 2) if net else nan,
        "e2e_lat_avg_ms": round(sum(e2e) / len(e2e), 2) if e2e else nan,
        "e2e_lat_p95_ms": round(_pct(e2e, 95), 2),
        "e2e_lat_max_ms": round(max(e2e), 2) if e2e else nan,
        "retransmissions": retx,
    }


if __name__ == "__main__":
    for k, v in analyze(sys.argv[1]).items():
        print("%-20s %s" % (k, v))
