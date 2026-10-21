#!/usr/bin/env python3
"""
Mininet topology for the Emergency Notification Network.

   srv(10.0.0.1)  bgs(10.0.0.2)                 c1..c4 (10.0.0.3-.6)  bgr(10.0.0.7)
        \\          /                                  \\     |     /     /
         +---- s1 ====== 10 Mbps bottleneck ====== s2 ----+-----+-----+
                (s1-eth3)                       (s2-eth1)

 - srv  : emergency server        - c1..c4 : subscribers
 - bgs/bgr : background traffic generator / sink (creates congestion on the s1<->s2 trunk)
 - The trunk is a real bottleneck: an OVS linux-htb QoS with 2 queues on both trunk ports
     queue 0 = best-effort (priority 1, min 1 Mbps)   queue 1 = emergency (priority 0, min 2 Mbps)
Run standalone for an interactive demo (start the Ryu controller first).
"""
import subprocess
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import Link, TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

TRUNK_BPS = 10_000_000
N_CLIENTS = 4
CTRL_MAC = "02:00:00:00:00:fe"


def build_net(loss=0, controller_ip="127.0.0.1", controller_port=6653):
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=Link, autoSetMacs=True, build=False)
    net.addController("c0", controller=RemoteController, ip=controller_ip, port=controller_port)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")
    srv = net.addHost("srv", ip="10.0.0.1/24")
    bgs = net.addHost("bgs", ip="10.0.0.2/24")
    clients = [net.addHost("c%d" % i, ip="10.0.0.%d/24" % (i + 2)) for i in range(1, N_CLIENTS + 1)]
    bgr = net.addHost("bgr", ip="10.0.0.7/24")
    # link order fixes port numbers: s1: srv=1 bgs=2 trunk=3 | s2: trunk=1 c1..c4=2..5 bgr=6
    net.addLink(srv, s1)
    net.addLink(bgs, s1)
    net.addLink(s1, s2)
    for c in clients:
        if loss > 0:
            net.addLink(c, s2, cls=TCLink, loss=loss)      # lossy last-mile (netem)
        else:
            net.addLink(c, s2)
    net.addLink(bgr, s2)
    return net


def _sh(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def setup_qos(net, rate=TRUNK_BPS):
    """HTB QoS with an emergency queue (1) and best-effort queue (0) on both trunk ports."""
    link = net.linksBetween(net.get("s1"), net.get("s2"))[0]
    for intf in (link.intf1, link.intf2):
        _sh(["ovs-vsctl", "--", "set", "port", intf.name, "qos=@qos",
             "--", "--id=@qos", "create", "qos", "type=linux-htb",
             "other-config:max-rate=%d" % rate, "queues:0=@q0", "queues:1=@q1",
             "--", "--id=@q0", "create", "queue", "other-config:min-rate=1000000",
             "other-config:max-rate=%d" % rate, "other-config:priority=1", "external_ids:name=be",
             "--", "--id=@q1", "create", "queue", "other-config:min-rate=2000000",
             "other-config:max-rate=%d" % rate, "other-config:priority=0", "external_ids:name=emerg"])
    info("*** QoS queues installed on %s, %s\n" % (link.intf1.name, link.intf2.name))


def prepare(net):
    """Everything that must happen after net.start()."""
    setup_qos(net)
    # static ARP for the virtual controller address used by the server's control messages
    net.get("srv").cmd("ip neigh replace 10.0.0.254 lladdr %s dev srv-eth0 nud permanent" % CTRL_MAC)


if __name__ == "__main__":
    setLogLevel("info")
    n = build_net()
    n.start()
    n.waitConnected()
    prepare(n)
    CLI(n)
    n.stop()
