#!/usr/bin/env bash
# Install/refresh the airrelay systemd service.
set -euo pipefail
INSTALL_DIR="/opt/airrelay"
if [[ $EUID -ne 0 ]]; then echo "Run as root"; exit 1; fi
if [[ ! -f "$INSTALL_DIR/services/airrelay.service" ]]; then
  echo "Service file not found. Run from the project or place it at /opt/airrelay."
  exit 1
fi
cp "$INSTALL_DIR/services/airrelay.service" /etc/systemd/system/airrelay.service
systemctl daemon-reload
systemctl enable airrelay.service
echo "airrelay.service installed & enabled."
echo "Start it with: scripts/control.sh start"
