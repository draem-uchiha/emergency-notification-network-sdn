#!/usr/bin/env python3
"""Emergency notification server (UDP).

 - Subscribers REGISTER over UDP.
 - An operator/publisher sends PUBLISH; the server fans out an ALERT to every subscriber.
 - CRITICAL/HIGH alerts need an ACK; unacked alerts are retransmitted.
 - The server tells the SDN controller about application events (SUBSCRIBER_ADDED,
   ALERT_START, ALERT_END) with small UDP control datagrams to a virtual controller IP.
"""
import argparse, csv, os, socket, threading, time
from common import *


class Server:
    def __init__(self, a):
        self.a = a
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, DSCP_EF_TOS)  # mark as EF
        self.sock.bind((a.bind, a.port))
        self.ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.subs = {}       # sub_id -> (ip, port)
        self.pending = {}    # (seq, sub_id) -> state for retransmission
        self.lock = threading.Lock()
        self.seq = 0
        self.alert_active = False
        self.last_alert = 0.0
        self.last_start_evt = 0.0
        os.makedirs(a.log_dir, exist_ok=True)
        self.sent_f = open(os.path.join(a.log_dir, "sent.csv"), "w", newline="")
        self.sent_w = csv.writer(self.sent_f)
        self.sent_w.writerow(["seq", "sub", "sev", "ts", "attempt", "ack_required"])
        self.ack_f = open(os.path.join(a.log_dir, "acks.csv"), "w", newline="")
        self.ack_w = csv.writer(self.ack_f)
        self.ack_w.writerow(["seq", "sub", "ack_ts", "attempts"])

    # ---- SDN control channel -------------------------------------------------
    def ctrl(self, **ev):
        if self.a.no_ctrl:
            return
        try:
            self.ctrl_sock.sendto(enc(ev), (CTRL_IP, CTRL_PORT))
        except OSError as e:
            print("[server] control send failed:", e, flush=True)

    # ---- sending ---------------------------------------------------------------
    def _send_alert(self, pkt, addr, sub):
        self.sock.sendto(enc(pkt), addr)
        self.sent_w.writerow([pkt["seq"], sub, pkt["sev"], "%.6f" % pkt["ts"], pkt["attempt"], int(pkt["ack"])])
        self.sent_f.flush()

    def on_register(self, m, addr):
        sub = m.get("id")
        if not sub:
            return
        new = self.subs.get(sub) != addr
        self.subs[sub] = addr
        self.sock.sendto(enc({"type": "REGISTER_OK", "id": sub}), addr)
        if new:
            print("[server] subscriber %s registered from %s:%d (total %d)" % (sub, addr[0], addr[1], len(self.subs)), flush=True)
            self.ctrl(event="SUBSCRIBER_ADDED", ip=addr[0], id=sub)

    def on_publish(self, m):
        sev = str(m.get("severity", "CRITICAL")).upper()
        now = time.time()
        if now - self.last_start_evt > 1.0:               # tell SDN an emergency is in progress
            first = not self.alert_active
            self.ctrl(event="ALERT_START", severity=sev)
            self.last_start_evt = now
            if first:
                time.sleep(0.15)                           # give controller time to reconfigure
        self.alert_active = True
        self.last_alert = time.time()
        with self.lock:
            self.seq += 1
            seq = self.seq
            ts = time.time()
            pkt = {"type": "ALERT", "seq": seq, "sev": sev, "text": m.get("text", ""),
                   "ts": ts, "attempt": 0, "ack": sev in RELIABLE}
            for sub, addr in list(self.subs.items()):
                self._send_alert(pkt, addr, sub)
                if pkt["ack"]:
                    self.pending[(seq, sub)] = {"pkt": pkt, "addr": addr, "tries": 0,
                                                "next": time.time() + self.a.rto}

    def on_ack(self, m):
        key = (m.get("seq"), m.get("id"))
        with self.lock:
            st = self.pending.pop(key, None)
            if st:
                self.ack_w.writerow([key[0], key[1], "%.6f" % time.time(), st["tries"] + 1])
                self.ack_f.flush()

    # ---- background threads ------------------------------------------------------
    def retransmit_loop(self):
        while True:
            time.sleep(0.05)
            now = time.time()
            with self.lock:
                for key, st in list(self.pending.items()):
                    if now < st["next"]:
                        continue
                    if st["tries"] >= self.a.max_retries:
                        del self.pending[key]
                        print("[server] DELIVERY FAILED seq=%s sub=%s" % key, flush=True)
                        continue
                    st["tries"] += 1
                    pkt = dict(st["pkt"])
                    pkt["attempt"] = st["tries"]
                    self._send_alert(pkt, st["addr"], key[1])
                    st["next"] = now + self.a.rto

    def watchdog_loop(self):
        while True:
            time.sleep(0.5)
            if self.alert_active and time.time() - self.last_alert > 2.0:
                self.alert_active = False
                self.ctrl(event="ALERT_END")

    def run(self):
        threading.Thread(target=self.retransmit_loop, daemon=True).start()
        threading.Thread(target=self.watchdog_loop, daemon=True).start()
        print("[server] listening on %s:%d" % (self.a.bind, self.a.port), flush=True)
        while True:
            data, addr = self.sock.recvfrom(4096)
            m = dec(data)
            if not m:
                continue
            t = m.get("type")
            if t == "REGISTER":
                self.on_register(m, addr)
            elif t == "UNREGISTER":
                self.subs.pop(m.get("id"), None)
            elif t == "ACK":
                self.on_ack(m)
            elif t == "PUBLISH":
                self.on_publish(m)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=SERVER_PORT)
    p.add_argument("--log-dir", default="results/live")
    p.add_argument("--rto", type=float, default=0.3, help="retransmission timeout (s)")
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--no-ctrl", action="store_true", help="do not talk to SDN controller (local tests)")
    try:
        Server(p.parse_args()).run()
    except KeyboardInterrupt:
        pass
