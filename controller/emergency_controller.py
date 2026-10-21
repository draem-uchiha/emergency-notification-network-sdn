"""
Emergency Notification Network - Ryu OpenFlow 1.3 controller
============================================================
Two modes (env SDN_MODE):
  priority  (default) : SDN-aware. Classifies emergency traffic, forwards it through the
                        high-priority OVS queue, reacts to application events and congestion.
  baseline            : static learning switch. No classification, everything in one queue.

What it does in "priority" mode
  1. CLASSIFY  - UDP src/dst port 9000/9001 or DSCP 46 (set by the server socket) = emergency.
  2. PRIORITISE- emergency flows: priority 300 + set_queue(1) (HTB queue with guaranteed rate
                 and top priority). Everything else uses queue 0.
  3. SOCKET->SDN INTEGRATION - the server sends control datagrams to a virtual IP (10.0.0.254).
                 Every switch punts them to this controller:
                   SUBSCRIBER_ADDED -> pre-install emergency flows for the new subscriber
                   ALERT_START      -> "emergency mode": throttle best-effort queue
                   ALERT_END / 5 s silence -> restore best-effort rate
  4. MONITOR   - polls trunk-port stats every second, logs utilisation, flags congestion.
"""
import json
import os
import subprocess
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ipv4, udp, arp, ether_types
from ryu.ofproto import ofproto_v1_3

MODE = os.environ.get("SDN_MODE", "priority")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results/live")
SERVER_IP = "10.0.0.1"
CTRL_IP, CTRL_PORT = "10.0.0.254", 9999
APP_PORTS = (9000, 9001)
EF_DSCP = 46
Q_BE, Q_EMERG = 0, 1
LINK_BPS = int(os.environ.get("LINK_BPS", 10_000_000))
BE_NORMAL_BPS = LINK_BPS
BE_THROTTLED_BPS = int(os.environ.get("BE_THROTTLED_BPS", 3_000_000))
TRUNK_PORT = {1: 3, 2: 1}          # dpid -> trunk (s1<->s2) port number
PRIO_PUNT, PRIO_EMERG, PRIO_UDP, PRIO_L2 = 500, 300, 200, 100


class EmergencySDN(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.mac_to_port = {}       # dpid -> {mac: port}
        self.ip_to_mac = {}
        self.emergency_mode = False
        self.last_alert = 0.0
        self.prev_tx = {}
        self.congested = {}
        os.makedirs(RESULTS_DIR, exist_ok=True)
        self.evlog = open(os.path.join(RESULTS_DIR, "controller_events.log"), "a")
        self.util_f = open(os.path.join(RESULTS_DIR, "trunk_util.csv"), "w")
        self.util_f.write("time,dpid,port,tx_mbps,util\n")
        self.log("controller started in %s mode" % MODE)
        hub.spawn(self._monitor)

    def log(self, msg):
        line = "%.3f %s" % (time.time(), msg)
        self.logger.info(line)
        self.evlog.write(line + "\n")
        self.evlog.flush()

    # ------------------------------------------------------------------ helpers
    def add_flow(self, dp, prio, match, actions, idle=0):
        p = dp.ofproto_parser
        inst = [p.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match,
                                 instructions=inst, idle_timeout=idle))

    @staticmethod
    def is_emergency(ip, u):
        return (u.src_port in APP_PORTS or u.dst_port in APP_PORTS or (ip.tos >> 2) == EF_DSCP)

    def actions_for(self, dp, out_port, emergency):
        p = dp.ofproto_parser
        acts = []
        if MODE == "priority" and emergency and out_port != dp.ofproto.OFPP_FLOOD:
            acts.append(p.OFPActionSetQueue(Q_EMERG))
        acts.append(p.OFPActionOutput(out_port))
        return acts

    def udp_match(self, dp, in_port, src, dst, sport, dport):
        return dp.ofproto_parser.OFPMatch(in_port=in_port, eth_type=0x0800, ip_proto=17,
                                          ipv4_src=src, ipv4_dst=dst, udp_src=sport, udp_dst=dport)

    # ------------------------------------------------------------------ switch setup
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features(self, ev):
        dp = ev.msg.datapath
        ofp, p = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        to_ctrl = [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, p.OFPMatch(), to_ctrl)                                   # table-miss
        self.add_flow(dp, PRIO_PUNT, p.OFPMatch(eth_type=0x0800, ip_proto=17,         # control channel
                                                ipv4_dst=CTRL_IP, udp_dst=CTRL_PORT), to_ctrl)
        self.log("switch s%d connected" % dp.id)

    # ------------------------------------------------------------------ packet-in
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, p = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        ip = pkt.get_protocol(ipv4.ipv4)
        u = pkt.get_protocol(udp.udp)
        a = pkt.get_protocol(arp.arp)

        # learn
        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port
        if ip:
            self.ip_to_mac[ip.src] = eth.src
        if a:
            self.ip_to_mac[a.src_ip] = a.src_mac

        # application -> SDN control message
        if ip and u and ip.dst == CTRL_IP and u.dst_port == CTRL_PORT:
            payload = pkt.protocols[-1] if isinstance(pkt.protocols[-1], (bytes, bytearray)) else b""
            self.control_event(payload)
            return

        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        emergency = bool(ip and u and self.is_emergency(ip, u))
        actions = self.actions_for(dp, out_port, emergency)

        if out_port != ofp.OFPP_FLOOD:
            if ip and u:
                match = self.udp_match(dp, in_port, ip.src, ip.dst, u.src_port, u.dst_port)
                prio = PRIO_EMERG if emergency else PRIO_UDP
                if emergency:
                    self.log("s%d classified EMERGENCY flow %s:%d -> %s:%d (queue %d)" % (
                        dp.id, ip.src, u.src_port, ip.dst, u.dst_port, Q_EMERG if MODE == "priority" else 0))
            elif ip:
                match = p.OFPMatch(in_port=in_port, eth_type=0x0800, ip_proto=ip.proto,
                                   eth_src=eth.src, eth_dst=eth.dst)
                prio = PRIO_L2
            else:
                match = p.OFPMatch(in_port=in_port, eth_type=eth.ethertype, eth_src=eth.src, eth_dst=eth.dst)
                prio = PRIO_L2
            self.add_flow(dp, prio, match, actions, idle=30)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port,
                                   actions=actions, data=data))

    # ------------------------------------------------------------------ socket -> SDN events
    def control_event(self, payload):
        try:
            ev = json.loads(payload.decode())
        except Exception:
            return
        name = ev.get("event")
        self.log("APP EVENT %s %s" % (name, json.dumps(ev)))
        if MODE != "priority":
            return
        if name == "ALERT_START":
            self.last_alert = time.time()
            self.set_emergency_mode(True)
        elif name == "ALERT_END":
            self.set_emergency_mode(False)
        elif name == "SUBSCRIBER_ADDED":
            self.preinstall(ev.get("ip"))

    def preinstall(self, sub_ip):
        """Proactively install high-priority flows server<->subscriber on every switch."""
        smac, dmac = self.ip_to_mac.get(SERVER_IP), self.ip_to_mac.get(sub_ip)
        if not smac or not dmac:
            return
        for dpid, dp in self.datapaths.items():
            tbl = self.mac_to_port.get(dpid, {})
            if smac not in tbl or dmac not in tbl:
                continue
            s_port, d_port = tbl[smac], tbl[dmac]
            self.add_flow(dp, PRIO_EMERG, self.udp_match(dp, s_port, SERVER_IP, sub_ip, 9000, 9001),
                          self.actions_for(dp, d_port, True), idle=0)
            self.add_flow(dp, PRIO_EMERG, self.udp_match(dp, d_port, sub_ip, SERVER_IP, 9001, 9000),
                          self.actions_for(dp, s_port, True), idle=0)
        self.log("pre-installed emergency flows for subscriber %s" % sub_ip)

    def set_emergency_mode(self, on):
        if on == self.emergency_mode:
            return
        self.emergency_mode = on
        rate = BE_THROTTLED_BPS if on else BE_NORMAL_BPS
        self.set_be_rate(rate)
        self.log("EMERGENCY MODE %s: best-effort queue max-rate -> %.1f Mbps" % ("ON" if on else "OFF", rate / 1e6))

    @staticmethod
    def set_be_rate(bps):
        try:
            out = subprocess.run(["ovs-vsctl", "--bare", "--columns=_uuid", "find", "queue",
                                  "external_ids:name=be"], capture_output=True, text=True, timeout=5).stdout.split()
            for uuid in out:
                subprocess.run(["ovs-vsctl", "set", "queue", uuid, "other-config:max-rate=%d" % bps],
                               timeout=5, capture_output=True)
        except Exception as e:
            print("set_be_rate failed:", e)

    # ------------------------------------------------------------------ monitoring
    def _monitor(self):
        while True:
            hub.sleep(1)
            if self.emergency_mode and time.time() - self.last_alert > 5:
                self.set_emergency_mode(False)
            for dpid, dp in list(self.datapaths.items()):
                if dpid in TRUNK_PORT:
                    dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, TRUNK_PORT[dpid]))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def stats_reply(self, ev):
        dp = ev.msg.datapath
        port = TRUNK_PORT.get(dp.id)
        for st in ev.msg.body:
            if st.port_no != port:
                continue
            now = time.time()
            prev = self.prev_tx.get(dp.id)
            if prev and now > prev[0]:
                bps = (st.tx_bytes - prev[1]) * 8 / (now - prev[0])
                util = bps / LINK_BPS
                self.util_f.write("%.3f,%d,%d,%.3f,%.3f\n" % (now, dp.id, port, bps / 1e6, util))
                self.util_f.flush()
                cong = util > 0.85
                if cong != self.congested.get(dp.id, False):
                    self.congested[dp.id] = cong
                    self.log("s%d trunk %s (%.1f Mbps, %.0f%%)" % (
                        dp.id, "CONGESTED" if cong else "congestion cleared", bps / 1e6, util * 100))
            self.prev_tx[dp.id] = (now, st.tx_bytes)
