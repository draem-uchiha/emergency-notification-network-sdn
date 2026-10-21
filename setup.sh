#!/bin/bash
# One-shot setup for Ubuntu 22.04 (Python 3.10). Safe to re-run (it rebuilds .venv).
set -e
cd "$(dirname "$0")"

echo "### 1/4 system packages"
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-venv python3-pip python3-matplotlib git
sudo systemctl enable --now openvswitch-switch || true

echo "### 2/4 python environment"
rm -rf .venv
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -U pip wheel "setuptools==65.5.0" pbr
pip install "eventlet==0.33.3" "dnspython==2.2.1"

echo "### 3/4 Ryu controller"
PBR_VERSION=4.34 pip install --no-build-isolation --no-cache-dir ryu==4.34
# Ryu 4.34 + newer eventlet: replace one removed (indented) import. Harmless if absent.
W=$(python -c "import ryu,os;print(os.path.join(os.path.dirname(ryu.__file__),'app','wsgi.py'))")
sed -i 's/^\([[:space:]]*\)from eventlet.wsgi import ALREADY_HANDLED.*/\1ALREADY_HANDLED = b""/' "$W"

echo "### 4/4 checks"
python -m ryu.cmd.manager --version
sudo mn -c >/dev/null 2>&1 || true
sudo mn --test pingall
echo "SETUP OK  -> next: ./verify.sh"
