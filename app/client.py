#!/usr/bin/env python3
"""Emergency notification subscriber (UDP client): registers, receives alerts, ACKs them."""
import argparse, csv, os, socket, time
from common import *


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--server", required=True)
    p.add_argument("--server-port", type=int, default=SERVER_PORT)
    p.add_argument("--bind-port", type=int, default=CLIENT_PORT)
    p.add_argument("--log-dir", default="results/live")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()

    os.makedirs(a.log_dir, exist_ok=True)
    f = open(os.path.join(a.log_dir, "recv_%s.csv" % a.id), "w", newline="")
    w = csv.writer(f)
    w.writerow(["sub", "seq", "attempt", "sev", "sent_ts", "recv_ts", "dup"])

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", a.bind_port))
    server = (a.server, a.server_port)

    # --- registration (retry until REGISTER_OK) ---
    s.settimeout(1.0)
    while True:
        s.sendto(enc({"type": "REGISTER", "id": a.id}), server)
        try:
            m = dec(s.recvfrom(4096)[0])
            if m and m.get("type") == "REGISTER_OK":
                print("[%s] registered" % a.id, flush=True)
                break
        except socket.timeout:
            pass

    # --- receive loop ---
    s.settimeout(None)
    seen = set()
    while True:
        data, _ = s.recvfrom(4096)
        now = time.time()
        m = dec(data)
        if not m or m.get("type") != "ALERT":
            continue
        seq = m["seq"]
        if m.get("ack"):                                   # always ACK (previous ACK may be lost)
            s.sendto(enc({"type": "ACK", "seq": seq, "id": a.id}), server)
        dup = seq in seen
        seen.add(seq)
        w.writerow([a.id, seq, m.get("attempt", 0), m["sev"], "%.6f" % m["ts"], "%.6f" % now, int(dup)])
        f.flush()
        if not a.quiet and not dup:
            print("[%s] ALERT #%d %s: %s (%.1f ms)" % (a.id, seq, m["sev"], m.get("text", "")[:40],
                                                      (now - m["ts"]) * 1000), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
