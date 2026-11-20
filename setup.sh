#!/bin/bash
# =============================================================
# setup.sh — Install Arize Phoenix and the demo app on a Linux VM
# as systemd services that start automatically on boot.
#
#   phoenix              Phoenix UI + OTLP collector   :6006 (HTTP), :4317 (gRPC)
#   phoenix-demo-app     Claude support assistant      :8000
#   phoenix-demo-loadgen steady demo traffic (skip with LOADGEN=0)
#
# Run from this directory:   sudo bash setup.sh
# Real Claude calls:         sudo ANTHROPIC_API_KEY=sk-ant-... bash setup.sh
# Phoenix only:              sudo APP=0 bash setup.sh
# Safe to re-run: it upgrades packages and redeploys. Existing /etc/default files are kept.
# Supports Ubuntu 22.04+/Debian 12+ (apt) and RHEL/Rocky/Alma 9+/Amazon Linux 2023 (dnf).
# =============================================================
set -euo pipefail

INSTALL_DIR="/opt/phoenix"
DATA_DIR="/var/lib/phoenix"
SVC_USER="phoenix"
APP="${APP:-1}"
LOADGEN="${LOADGEN:-1}"
[[ "$APP" == "1" ]] || LOADGEN=0

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash $0" >&2
  exit 1
fi
cd "$(dirname "$(readlink -f "$0")")"

find_python() {
  for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,14)))'; then
      command -v "$p"; return 0
    fi
  done
  return 1
}

echo "=============================="
echo "1. Installing Python 3.11+"
echo "=============================="
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3 python3-venv curl
  if ! find_python >/dev/null; then
    apt-get install -y python3.11 python3.11-venv || true  # Ubuntu 22.04 ships 3.10 as python3
  fi
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 curl
  find_python >/dev/null || dnf install -y python3.12 || dnf install -y python3.11 || true
else
  echo "Unsupported distro: install Python 3.11-3.13 manually, then re-run." >&2
  exit 1
fi
PY="$(find_python)" || { echo "Python 3.11-3.13 not found after install." >&2; exit 1; }
echo "Using $PY ($("$PY" --version))"

echo "=============================="
echo "2. Creating service user & directories"
echo "=============================="
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 750 "$DATA_DIR"
install -d -m 755 "$INSTALL_DIR"

echo "=============================="
echo "3. Installing Phoenix server"
echo "=============================="
[[ -x "$INSTALL_DIR/server-venv/bin/python" ]] || "$PY" -m venv "$INSTALL_DIR/server-venv"
"$INSTALL_DIR/server-venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/server-venv/bin/pip" install --quiet -r server/requirements.txt
[[ -f /etc/default/phoenix ]] || install -m 644 deploy/phoenix.env /etc/default/phoenix
install -m 644 deploy/phoenix.service /etc/systemd/system/phoenix.service

if [[ "$APP" == "1" ]]; then
  echo "=============================="
  echo "4. Installing demo app"
  echo "=============================="
  [[ -x "$INSTALL_DIR/app-venv/bin/python" ]] || "$PY" -m venv "$INSTALL_DIR/app-venv"
  "$INSTALL_DIR/app-venv/bin/pip" install --quiet --upgrade pip
  "$INSTALL_DIR/app-venv/bin/pip" install --quiet -r app/requirements.txt
  rm -rf "$INSTALL_DIR/app"
  cp -r app "$INSTALL_DIR/app"
  find "$INSTALL_DIR/app" -name __pycache__ -prune -exec rm -rf {} +

  # The app env file can hold an API key: readable by root and the service user only.
  if [[ ! -f /etc/default/phoenix-demo-app ]]; then
    install -m 640 -g "$SVC_USER" deploy/phoenix-demo-app.env /etc/default/phoenix-demo-app
  fi
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    sed -i '/^#\? *ANTHROPIC_API_KEY=/d' /etc/default/phoenix-demo-app
    echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" >> /etc/default/phoenix-demo-app
    echo "Saved ANTHROPIC_API_KEY to /etc/default/phoenix-demo-app"
  fi
  install -m 644 deploy/phoenix-demo-app.service /etc/systemd/system/phoenix-demo-app.service

  if [[ "$LOADGEN" == "1" ]]; then
    [[ -f /etc/default/phoenix-demo-loadgen ]] || install -m 644 deploy/phoenix-demo-loadgen.env /etc/default/phoenix-demo-loadgen
    install -m 644 deploy/phoenix-demo-loadgen.service /etc/systemd/system/phoenix-demo-loadgen.service
  fi
fi

echo "=============================="
echo "5. Starting services"
echo "=============================="
systemctl daemon-reload
systemctl enable phoenix
systemctl restart phoenix
echo -n "Waiting for Phoenix on :6006 "
for _ in $(seq 1 60); do
  curl -fs -o /dev/null http://localhost:6006/healthz && break
  echo -n "."; sleep 2
done
echo

if [[ "$APP" == "1" ]]; then
  systemctl enable phoenix-demo-app
  systemctl restart phoenix-demo-app
  if [[ "$LOADGEN" == "1" ]]; then
    systemctl enable phoenix-demo-loadgen
    systemctl restart phoenix-demo-loadgen
  fi
fi

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Done."
echo "  Phoenix UI:        http://${HOST_IP:-<vm-ip>}:6006"
echo "  OTLP collector:    http://${HOST_IP:-<vm-ip>}:6006/v1/traces  (gRPC :4317)"
if [[ "$APP" == "1" ]]; then
  echo "  Demo app:          http://${HOST_IP:-<vm-ip>}:8000"
  echo "  Mode:              $(curl -fs --retry 10 --retry-connrefused --retry-delay 1 http://localhost:8000/health || echo 'not up yet: journalctl -u phoenix-demo-app')"
fi
echo "Logs: journalctl -u phoenix -u phoenix-demo-app -u phoenix-demo-loadgen -f"

# With login on, Phoenix answers 401 to traces without an API key, and the app only logs it.
if [[ "$APP" == "1" ]] && grep -qiE '^[[:space:]]*PHOENIX_ENABLE_AUTH=["'\'']?true' /etc/default/phoenix \
   && ! grep -qE '^[[:space:]]*PHOENIX_API_KEY=[^[:space:]#]+' /etc/default/phoenix-demo-app; then
  echo
  echo "WARNING: Phoenix login is on but /etc/default/phoenix-demo-app has no PHOENIX_API_KEY,"
  echo "         so traces are rejected (401). Create a System key in Phoenix (Settings > API Keys),"
  echo "         set PHOENIX_API_KEY in that file, then: sudo systemctl restart phoenix-demo-app"
fi
