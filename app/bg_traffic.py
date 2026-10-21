#!/usr/bin/env python3
"""Background (non-emergency) UDP traffic generator/sink used to create congestion."""
import argparse, socket, time

p = argparse.ArgumentParser()
p.add_argument("--mode", choices=["send", "sink"], required=True)
p.add_argument("--dst", default="10.0.0.7")
p.add_argument("--port", type=int, default=5001)
p.add_argument("--rate-mbps", type=float, default=10.0)
p.add_argument("--duration", type=float, default=30.0)
p.add_argument("--size", type=int, default=1200)
a = p.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
if a.mode == "sink":
    s.bind(("0.0.0.0", a.port))
    while True:
        s.recvfrom(65535)
else:
    payload = b"b" * a.size
    gap = a.size * 8 / (a.rate_mbps * 1e6)
    end = time.perf_counter() + a.duration
    nxt = time.perf_counter()
    while time.perf_counter() < end:
        s.sendto(payload, (a.dst, a.port))
        nxt += gap
        d = nxt - time.perf_counter()
        if d > 0:
            time.sleep(d)
