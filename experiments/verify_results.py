#!/usr/bin/env python3
"""PASS/FAIL sanity check of results/summary.csv (used by verify.sh)."""
import csv, math, sys

path = sys.argv[1] if len(sys.argv) > 1 else "results/summary.csv"
rows = list(csv.DictReader(open(path, newline="")))
f = lambda r, k: float(r[k])
checks = []

checks.append(("every run produced data (no empty/NaN runs)",
               bool(rows) and all(int(r["sent"]) > 0 and not math.isnan(f(r, "net_lat_avg_ms")) for r in rows)))

A = [r for r in rows if r["experiment"] == "A"]
B = [r for r in rows if r["experiment"] == "B"]
checks.append(("SDN delivers 100% of alerts in every congestion run",
               all(f(r, "residual_loss_pct") == 0 for r in A if r["mode"] == "priority")))
if A:
    top = max(f(r, "load_mbps") for r in A)
    base = [r for r in A if r["mode"] == "baseline" and f(r, "load_mbps") == top]
    prio = [r for r in A if r["mode"] == "priority" and f(r, "load_mbps") == top]
    if base and prio:
        b, p = f(base[0], "net_lat_avg_ms"), f(prio[0], "net_lat_avg_ms")
        checks.append(("at %g Mbps load: baseline latency (%.2f ms) is >5x SDN latency (%.2f ms)" % (top, b, p), b > 5 * p))
if B:
    checks.append(("ACK/retransmission removes loss (residual < raw loss, residual == 0)",
                   all(f(r, "residual_loss_pct") == 0 and f(r, "first_try_loss_pct") > 0 for r in B)))

ok = True
for name, passed in checks:
    print(("PASS  " if passed else "FAIL  ") + name)
    ok &= passed
print("\nOVERALL:", "PASS - this machine reproduces the results" if ok else "FAIL - see runme.md troubleshooting")
sys.exit(0 if ok else 1)
