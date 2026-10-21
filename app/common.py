"""Shared constants and helpers for the Emergency Notification Network."""
import json

SERVER_PORT = 9000          # server listens here; alerts are SENT from here
CLIENT_PORT = 9001          # subscribers listen here
CTRL_IP = "10.0.0.254"      # virtual "controller" address (punted to SDN controller)
CTRL_PORT = 9999
DSCP_EF_TOS = 0xB8          # DSCP 46 (Expedited Forwarding) << 2
RELIABLE = {"CRITICAL", "HIGH"}   # severities that need ACK + retransmission


def enc(d):
    return json.dumps(d, separators=(",", ":")).encode()


def dec(b):
    try:
        return json.loads(b.decode())
    except Exception:
        return None
