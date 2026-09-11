#!/usr/bin/env bash
# RF / SDR hardware bring-up diagnostics. Use during hardware testing.
# Usage: sudo bash scripts/diag_hardware.sh
set -euo pipefail
echo "======== AirRelay hardware diagnostic ========"
echo "--- USB devices (RF dongles) ---"
lsusb | grep -iE "realtek|hackrf|rtl|0bda|1d50" || echo "  (none matched)"
echo ""
echo "--- RTL-SDR ---"
if command -v rtl_test >/dev/null; then rtl_test -t 2>&1 | head -10
else echo "  rtl_test not found (is librtlsdr installed?)"; fi
echo ""
echo "--- HackRF ---"
if command -v hackrf_info >/dev/null; then hackrf_info 2>&1 | head -14
else echo "  hackrf_info not found"; fi
echo ""
echo "--- kernel DVB blacklist ---"
grep -r dvb_usb_rtl28xxu /etc/modprobe.d/ 2>/dev/null || echo "  warning: RTL DVB driver may still claim the dongle"
echo ""
echo "--- radio manager view ---"
PY="${PY:-python3}"
if [[ -x /opt/airrelay/venv/bin/python ]]; then PY=/opt/airrelay/venv/bin/python; fi
(cd /opt/airrelay && "$PY" -c "import sys;sys.path.insert(0,'src');from airrelay.cli import cmd_diagnose;cmd_diagnose(None)") 2>/dev/null \
  || echo "  (airrelay diagnose unavailable; start service first)"
echo "==============================================="
