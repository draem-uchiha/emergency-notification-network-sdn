# Emergency Notification Network (UDP + Mininet + SDN)

UDP-based emergency alert system. An SDN controller (Ryu, OpenFlow 1.3) classifies emergency
traffic, forwards it through a high-priority queue, and reacts to events sent by the application.
Evaluated against a static-switch baseline under congestion and lossy links.

## 1. Architecture
```
 srv (server) ─┐                                      ┌─ c1 .. c4 (subscribers)
 bgs (bg load) ─┴─ s1 ══ 10 Mbps bottleneck (2 HTB queues) ══ s2 ─┴─ bgr (bg sink)
                    │  OpenFlow 1.3                      │
                    └──────────── Ryu controller ────────┘
```
| Component | File | Role |
|---|---|---|
| Server | app/server.py | UDP :9000. Registers subscribers, fans out alerts, ACK + retransmit for CRITICAL/HIGH, sends control events to SDN, marks packets DSCP EF |
| Subscriber | app/client.py | UDP :9001. REGISTER, receives alerts, sends ACK, logs latency |
| Publisher | app/publisher.py | Operator console: asks server to broadcast N alerts |
| Background load | app/bg_traffic.py | UDP flood (port 5001) to create congestion |
| Controller | controller/emergency_controller.py | Classification, priority queue, event handling, monitoring |
| Topology | topology/topology.py | Mininet topology + OVS QoS queues |
| Experiments | experiments/run_experiments.py, analyze.py | Automated runs, metrics, plots |

**Protocol (JSON over UDP):** `REGISTER/REGISTER_OK`, `PUBLISH` (operator->server), `ALERT{seq,sev,ts,attempt,ack}`, `ACK{seq,id}`.
Severity INFO = fire-and-forget; HIGH/CRITICAL = ACK required, retransmit every 0.3 s, max 3 retries.

**Project expectations -> where implemented**
| Expectation | Implementation |
|---|---|
| UDP notification delivery | server.py / client.py raw `socket.SOCK_DGRAM` |
| Multiple subscribers | 4 clients register dynamically; server keeps a table |
| Classify emergency traffic | Controller: UDP port 9000/9001 or DSCP 46 -> emergency |
| High-priority SDN forwarding | Priority-300 flows + `set_queue(1)` into HTB priority queue |
| Delivery confirmation | ACK + retransmission (CRITICAL/HIGH) |
| Congestion scenarios | bg_traffic 0-15 Mbps over the 10 Mbps trunk |
| Latency / packet loss | analyze.py from server `sent.csv` and client `recv_*.csv` |

**Dynamic SDN behaviour (socket <-> SDN integration):** server sends UDP control datagrams to virtual IP 10.0.0.254
(punted to controller by a priority-500 flow on every switch):
- `SUBSCRIBER_ADDED` -> controller pre-installs emergency flows (no first-packet delay)
- `ALERT_START` -> "emergency mode": best-effort queue capped at 3 Mbps via `ovs-vsctl`
- `ALERT_END` (or 5 s silence) -> restored to 10 Mbps
- 1 s port-stats polling logs trunk utilisation and flags congestion (`results/*/trunk_util.csv`, `controller_events.log`)

## 2. Setup (Ubuntu 20.04/22.04 VM, as in the Mininet guide)
```bash
./setup.sh            # mininet, OVS, ryu (+eventlet 0.30.2 fix), matplotlib
sudo mn -c
```
`setup.sh` pins the versions that work on Ubuntu 22.04 / Python 3.10 (setuptools 65.5, eventlet 0.33.3, dnspython 2.2.1, Ryu 4.34 built with PBR_VERSION). Ryu is started as `python -m ryu.cmd.manager`.

## 3. Run everything automatically
```bash
sudo .venv/bin/python experiments/run_experiments.py --quick   # ~2 min smoke run
sudo .venv/bin/python experiments/run_experiments.py           # full: 13 runs, ~6 min
```
Outputs: `results/summary.csv`, `results/latency_vs_load.png`, `loss_vs_load.png`, `robustness_loss.png`,
plus per-run logs, flow-table dump (`flows_s1.txt`) and `tc` queue counters (`tc_trunk.txt`).

## 4. Manual demo (for the viva)
Terminal 1 (controller):
```bash
SDN_MODE=priority RESULTS_DIR=results/demo sudo -E .venv/bin/python -m ryu.cmd.manager controller/emergency_controller.py
```
Terminal 2 (topology, run from repo root):
```bash
sudo .venv/bin/python topology/topology.py
mininet> srv python3 app/server.py --log-dir results/demo > results/demo/server.out 2>&1 &
mininet> c1 python3 app/client.py --id c1 --server 10.0.0.1 --log-dir results/demo > results/demo/c1.out 2>&1 &
mininet> c2 python3 app/client.py --id c2 --server 10.0.0.1 --log-dir results/demo > results/demo/c2.out 2>&1 &
mininet> bgr python3 app/bg_traffic.py --mode sink &
mininet> bgs python3 app/bg_traffic.py --mode send --dst 10.0.0.7 --rate-mbps 14 --duration 40 &
mininet> srv python3 app/publisher.py --count 50 --interval 0.1 --severity CRITICAL
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1      # emergency flows prio 300, actions set_queue:1
mininet> sh tc -s class show dev s1-eth3               # per-queue packet counters
mininet> sh tail -n 5 results/demo/c1.out
```
Repeat with `SDN_MODE=baseline` to show the difference. (Create `results/demo` first: `mkdir -p results/demo`.)

## 5. Test cases
| # | Scenario | Expected |
|---|---|---|
| T1 | `pingall` / topology up | 0% loss, switches connected to Ryu |
| T2 | 4 clients register | server prints 4 subscribers; controller logs `SUBSCRIBER_ADDED` + pre-installed flows |
| T3 | Publish 100 CRITICAL, no load | 100% delivery, ~ms latency |
| T4 | Same with 12-15 Mbps background load | Baseline: high latency/loss; SDN: latency near T3 |
| T5 | INFO severity | No ACK, no retransmit |
| T6 | 10-20% link loss | raw loss ~= injected %, residual ~0 through retransmission |
| T7 | Alert burst ends | controller logs `EMERGENCY MODE OFF`, best-effort restored |

## 6. Metrics
`net_lat_avg_ms` (first-attempt delivery latency), `e2e_lat_*` (incl. retransmit delay), `first_try_loss_pct`
(raw packet loss), `residual_loss_pct` (loss after ACK/retransmit), `retransmissions`.
Clocks are comparable because all Mininet hosts share the VM clock.

## 7. Viva cheat-sheet
- *Why UDP?* Low latency, no head-of-line blocking; reliability added only where needed (ACK for CRITICAL).
- *How does SDN identify emergency traffic?* L4 ports + DSCP EF set by server via `IP_TOS`.
- *How is priority enforced?* OpenFlow `set_queue` -> OVS HTB queue 1 (priority 0, min 2 Mbps) on the bottleneck.
- *Where is the app/SDN integration?* Control datagrams -> packet-in -> controller reconfigures flows/queues.
- *Baseline?* Same topology and bottleneck, learning switch only, single queue.
- *Limitations:* single path (no reroute), stop-and-wait retransmission, QoS queues need OVS kernel datapath.

## 8. Repo / GitHub
```bash
git init && git add . && git commit -m "Emergency Notification Network" 
git remote add origin <your-repo-url> && git push -u origin main
```
Add your measured plots and a short results table (from `results/summary.csv`) under a "Results" heading before submission.
