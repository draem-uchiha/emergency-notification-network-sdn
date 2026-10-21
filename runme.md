# RUNME - run the Emergency Notification Network on any laptop

Everything below assumes **Ubuntu 22.04** (Python 3.10), either installed or in a VM
(VirtualBox/VMware, as in the course Mininet guide). Windows/macOS directly will NOT work,
and Ubuntu 24.04 will NOT work with the pinned versions - use a 22.04 VM.

All commands are run from the project folder in a terminal.

---

## 0. Get the folder onto the laptop
Either clone the GitHub repo, or unzip the shared zip, then:
```bash
cd ~/Downloads/emergency-notification-sdn      # adjust to wherever the folder is
```
(Do NOT copy an existing `.venv/` folder between laptops - it is rebuilt by setup.)

## 1. One-time setup (about 5 minutes, needs internet + sudo password)
```bash
chmod +x setup.sh verify.sh
./setup.sh
```
"already the newest version" messages from apt are fine.
Success looks like: `ryu-manager 4.34`, then `*** Results: 0% dropped`, then **`SETUP OK`**.

## 2. Verify this laptop reproduces our results (about 2 minutes)
```bash
./verify.sh
```
It checks the environment, runs the quick experiment, and prints PASS/FAIL lines.
Expected ending:
```
PASS  every run produced data (no empty/NaN runs)
PASS  SDN delivers 100% of alerts in every congestion run
PASS  at 12 Mbps load: baseline latency (~70 ms) is >5x SDN latency (~0.26 ms)
PASS  ACK/retransmission removes loss (residual < raw loss, residual == 0)
OVERALL: PASS
```
Exact numbers differ per machine; the pattern (baseline slow under load, SDN fast) must hold.

## 3. Full experiment run (about 8 minutes)
```bash
sudo mn -c
sudo .venv/bin/python experiments/run_experiments.py
```
Produces in `results/`: `summary.csv`, `latency_vs_load.png`, `loss_vs_load.png`, `robustness_loss.png`
and one folder per run containing `controller_events.log`, `flows_s1.txt`, `tc_trunk.txt`, `trunk_util.csv`.
Check the table any time with: `python3 experiments/verify_results.py results/summary.csv`

## 4. Live demo (for the presentation) - needs 2 terminals, both in the project folder
**Terminal 1 - controller** (try `SDN_MODE=baseline` later to show the contrast):
```bash
sudo mn -c
mkdir -p results/demo
SDN_MODE=priority RESULTS_DIR=results/demo sudo -E .venv/bin/python -m ryu.cmd.manager controller/emergency_controller.py
```
**Terminal 2 - network**:
```bash
sudo .venv/bin/python topology/topology.py
```
Then at the `mininet>` prompt, one line at a time:
```
srv python3 app/server.py --log-dir results/demo > results/demo/server.out 2>&1 &
c1 python3 app/client.py --id c1 --server 10.0.0.1 --log-dir results/demo > results/demo/c1.out 2>&1 &
c2 python3 app/client.py --id c2 --server 10.0.0.1 --log-dir results/demo > results/demo/c2.out 2>&1 &
bgr python3 app/bg_traffic.py --mode sink &
bgs python3 app/bg_traffic.py --mode send --dst 10.0.0.7 --rate-mbps 14 --duration 40 &
srv python3 app/publisher.py --count 50 --interval 0.1 --severity CRITICAL
sh ovs-ofctl -O OpenFlow13 dump-flows s1
sh tc -s class show dev s1-eth3
sh tail -n 5 results/demo/c1.out
sh cat results/demo/controller_events.log
exit
```
What to point out: emergency flows with `set_queue:1` in `dump-flows`, the `EMERGENCY MODE ON/OFF`
lines in `controller_events.log`, and low latency in `c1.out` despite 14 Mbps of background load.
Run it again with `SDN_MODE=baseline` in Terminal 1 and compare the latency in `c1.out`.

---

## Troubleshooting (every problem we actually hit)
| Symptom | Fix |
|---|---|
| `Unable to contact the remote controller at 127.0.0.1:6653`, all results NaN | Ryu crashed on startup. Run `cat results/A_baseline_load0_loss0/ryu.out`. If it says `cannot import name 'ALREADY_HANDLED'`: run `W=.venv/lib/python3.10/site-packages/ryu/app/wsgi.py; sed -i 's/^\([[:space:]]*\)from eventlet.wsgi import ALREADY_HANDLED.*/\1ALREADY_HANDLED = b""/' $W` |
| `Failed to build 'ryu'` / `Unknown distribution option: 'pbr'` | Use `./setup.sh` (installs `pbr` first and builds with `PBR_VERSION=4.34 --no-build-isolation`) |
| `is_timeout` / `MutableMapping` / `oslo` errors | Wrong package versions. Re-run `./setup.sh` - it rebuilds `.venv` with the pinned working set |
| `ovs-vsctl: database connection failed` | `sudo systemctl start openvswitch-switch` |
| Anything weird after a crash or Ctrl+C | `sudo mn -c`, then retry |
| `RTNETLINK answers: File exists` | `sudo mn -c` |
| Python is not 3.10 (e.g. Ubuntu 24.04) | Use an Ubuntu 22.04 VM |
| `Exception: Please shut down the controller which is running on port 6653` | `sudo pkill -f ryu.cmd.manager; sudo mn -c` |

Still stuck? Send the last 20 lines of the terminal output plus `results/<run folder>/ryu.out`.
