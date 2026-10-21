#!/bin/bash
# Checks the environment, runs the quick experiment, and prints PASS/FAIL.
cd "$(dirname "$0")"
bad=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "OK    $1"; else echo "FAIL  $1"; bad=1; fi; }
chk "Ubuntu/Linux with Python 3.10 venv"    "[ -x .venv/bin/python ] && .venv/bin/python -c 'import sys;assert sys.version_info[:2]==(3,10)'"
chk "Mininet python module importable"      ".venv/bin/python -c 'import mininet.net'"
chk "Open vSwitch running"                  "sudo ovs-vsctl show"
chk "Ryu 4.34 starts"                       ".venv/bin/python -m ryu.cmd.manager --version"
[ $bad -eq 1 ] && { echo; echo "Environment problem - run ./setup.sh (or see runme.md troubleshooting)"; exit 1; }
echo; echo "Running quick experiment (~2 min)..."
sudo mn -c >/dev/null 2>&1
sudo .venv/bin/python experiments/run_experiments.py --quick || exit 1
echo; .venv/bin/python experiments/verify_results.py results/summary.csv
