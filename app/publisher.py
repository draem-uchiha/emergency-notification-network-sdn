#!/usr/bin/env python3
"""Emergency operator console: asks the server to broadcast alerts."""
import argparse, socket, time
from common import *

p = argparse.ArgumentParser()
p.add_argument("--server", default="127.0.0.1")
p.add_argument("--port", type=int, default=SERVER_PORT)
p.add_argument("--count", type=int, default=1)
p.add_argument("--interval", type=float, default=0.1)
p.add_argument("--severity", default="CRITICAL", choices=["INFO", "HIGH", "CRITICAL"])
p.add_argument("--text", default="EVACUATE BUILDING A - FIRE DETECTED " + "x" * 60)
a = p.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for i in range(a.count):
    s.sendto(enc({"type": "PUBLISH", "severity": a.severity, "text": a.text}), (a.server, a.port))
    time.sleep(a.interval)
print("published %d %s alerts" % (a.count, a.severity))
