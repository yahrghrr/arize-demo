#!/bin/bash
# uninstall.sh — stop and remove the Phoenix services.
#   sudo bash uninstall.sh            keeps trace data in /var/lib/phoenix and the /etc/default files
#   sudo PURGE=1 bash uninstall.sh    removes those too
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Please run as root: sudo bash $0" >&2; exit 1; }

for svc in phoenix-demo-loadgen phoenix-demo-app phoenix; do
  systemctl disable --now "$svc" 2>/dev/null || true
  rm -f "/etc/systemd/system/${svc}.service"
done
systemctl daemon-reload
rm -rf /opt/phoenix

if [[ "${PURGE:-0}" == "1" ]]; then
  rm -f /etc/default/phoenix /etc/default/phoenix-demo-app /etc/default/phoenix-demo-loadgen
  rm -rf /var/lib/phoenix
  userdel phoenix 2>/dev/null || true
  echo "Removed services, config and trace data."
else
  echo "Removed services. Kept /var/lib/phoenix and /etc/default/phoenix*. PURGE=1 removes them."
fi
