#!/usr/bin/env python3
"""
Automated evaluation. Run as root:   sudo <venv>/bin/python experiments/run_experiments.py [--quick]

Experiment A (congestion sweep): background load 0/6/10/12/15 Mbps on the 10 Mbps trunk,
                 baseline (static switch) vs priority (SDN) controller.
Experiment B (robustness): lossy subscriber links (5/10/20 %) with SDN + ACK/retransmission, 12 Mbps load.
Results -> results/summary.csv and PNG plots.
"""
import argparse, csv, os, shutil, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "topology"))
sys.path.insert(0, str(ROOT / "experiments"))
from mininet.log import setLogLevel
from topology import build_net, prepare, N_CLIENTS
from analyze import analyze

PY = sys.executable
APP = ROOT / "app"


def start_controller(mode, run_dir):
    env = dict(os.environ, SDN_MODE=mode, RESULTS_DIR=str(run_dir), EVENTLET_NO_GREENDNS="yes")
    log = open(run_dir / "ryu.out", "w")
    # run Ryu as a module with the same (venv) python -> avoids broken ryu-manager launcher scripts
    return subprocess.Popen([PY, "-m", "ryu.cmd.manager", "--ofp-tcp-listen-port", "6653",
                             str(ROOT / "controller" / "emergency_controller.py")],
                            env=env, stdout=log, stderr=subprocess.STDOUT, preexec_fn=os.setsid)


def run_once(exp, mode, load, loss, out_root, n_alerts, interval):
    run_dir = out_root / ("%s_%s_load%s_loss%s" % (exp, mode, load, loss))
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    print("\n=== %s | mode=%s load=%s Mbps loss=%s%% ===" % (exp, mode, load, loss), flush=True)
    ctrl = start_controller(mode, run_dir)
    time.sleep(4)
    net, procs = None, []
    try:
        net = build_net(loss=loss)
        net.start()
        net.waitConnected(timeout=20)
        prepare(net)
        time.sleep(1)
        srv, bgs, bgr = net.get("srv"), net.get("bgs"), net.get("bgr")

        def spawn(host, args, name):
            procs.append(host.popen([PY] + [str(x) for x in args], stdout=open(run_dir / (name + ".out"), "w"),
                                    stderr=subprocess.STDOUT))

        spawn(srv, [APP / "server.py", "--log-dir", run_dir], "server")
        time.sleep(0.5)
        for i in range(1, N_CLIENTS + 1):
            spawn(net.get("c%d" % i), [APP / "client.py", "--id", "c%d" % i, "--server", "10.0.0.1",
                                       "--log-dir", run_dir, "--quiet"], "client%d" % i)
        time.sleep(3)                                   # registration + SUBSCRIBER_ADDED events
        if load > 0:
            spawn(bgr, [APP / "bg_traffic.py", "--mode", "sink"], "bgsink")
            spawn(bgs, [APP / "bg_traffic.py", "--mode", "send", "--dst", "10.0.0.7", "--rate-mbps", load,
                        "--duration", n_alerts * interval + 8], "bgsend")
            time.sleep(2)                               # let the queue fill (congestion established)
        srv.cmd("%s %s/publisher.py --server 127.0.0.1 --count %d --interval %s --severity CRITICAL"
                % (PY, APP, n_alerts, interval))
        time.sleep(4)                                   # allow retransmissions to finish
        # snapshot of flow table / queue stats as evidence
        with open(run_dir / "flows_s1.txt", "w") as f:
            f.write(net.get("s1").cmd("ovs-ofctl -O OpenFlow13 dump-flows s1"))
        with open(run_dir / "tc_trunk.txt", "w") as f:
            f.write(net.get("s1").cmd("tc -s class show dev s1-eth3"))
    finally:
        for p in procs:
            p.terminate()
        if net:
            net.stop()
        os.killpg(os.getpgid(ctrl.pid), signal.SIGTERM)
        time.sleep(1)
    m = analyze(run_dir)
    print(m, flush=True)
    return dict(experiment=exp, mode=mode, load_mbps=load, loss_pct=loss, **m)


def plots(rows, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - skipping plots")
        return
    A = [r for r in rows if r["experiment"] == "A"]
    for key, ylabel, fn in (("net_lat_avg_ms", "Avg notification latency (ms)", "latency_vs_load.png"),
                            ("first_try_loss_pct", "First-attempt packet loss (%)", "loss_vs_load.png")):
        plt.figure(figsize=(6, 4))
        for mode, lab in (("baseline", "Static switch (baseline)"), ("priority", "SDN priority")):
            pts = sorted((r["load_mbps"], r[key]) for r in A if r["mode"] == mode)
            if pts:
                plt.plot(*zip(*pts), marker="o", label=lab)
        plt.xlabel("Background load (Mbps) on 10 Mbps trunk")
        plt.ylabel(ylabel)
        plt.grid(alpha=.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(out / fn, dpi=150)
        plt.close()
    B = sorted((r["loss_pct"], r["first_try_loss_pct"], r["residual_loss_pct"]) for r in rows if r["experiment"] == "B")
    if B:
        plt.figure(figsize=(6, 4))
        plt.plot([b[0] for b in B], [b[1] for b in B], marker="o", label="Raw loss (1st attempt)")
        plt.plot([b[0] for b in B], [b[2] for b in B], marker="s", label="After ACK/retransmit")
        plt.xlabel("Injected link loss (%)")
        plt.ylabel("Notifications lost (%)")
        plt.grid(alpha=.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(out / "robustness_loss.png", dpi=150)
        plt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--alerts", type=int, default=100)
    ap.add_argument("--interval", type=float, default=0.1)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run with sudo")
    setLogLevel("warning")
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    loads = [0, 12] if a.quick else [0, 6, 10, 12, 15, 25]
    losses = [10] if a.quick else [5, 10, 20]
    n = 40 if a.quick else a.alerts
    rows = []
    for load in loads:
        for mode in ("baseline", "priority"):
            rows.append(run_once("A", mode, load, 0, out, n, a.interval))
    for loss in losses:
        rows.append(run_once("B", "priority", 12, loss, out, n, a.interval))
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    plots(rows, out)
    print("\nDone. See results/summary.csv and results/*.png")


if __name__ == "__main__":
    main()
