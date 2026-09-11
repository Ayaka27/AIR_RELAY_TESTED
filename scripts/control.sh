#!/usr/bin/env bash
# Operational control for the AirRelay system service.
# Usage: scripts/control.sh {start|stop|restart|status|logs|diagnose|enable|disable}
set -euo pipefail
SVC=airrelay
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT/venv"
PY="$VENV/bin/python"

case "${1:-}" in
  start)   systemctl start $SVC && echo "started"; journalctl -u $SVC -n 5 --no-pager ;;
  stop)    systemctl stop $SVC && echo "stopped" ;;
  restart) systemctl restart $SVC && echo "restarted"; journalctl -u $SVC -n 5 --no-pager ;;
  status)  systemctl status $SVC --no-pager ;;
  logs)    journalctl -u $SVC -f --no-pager ;;
  enable)  systemctl enable $SVC ;;
  disable) systemctl disable $SVC ;;
  diagnose)
     echo "=== hardware ==="; rtl_test -t 2>&1 | head -6 || echo "rtl_test unavailable"
     echo "--- hackrf ---";  hackrf_info 2>&1 | head -8 || echo "hackrf_info unavailable"
     echo "=== airrelay ==="; $PY -m airrelay diagnose || \
         AIRRELAY_HOME="$PROJECT" $PY -c "import sys;sys.path.insert(0,'src');from airrelay.cli import cmd_diagnose;cmd_diagnose(None)"
     ;;
  *) echo "usage: $0 {start|stop|restart|status|logs|diagnose|enable|disable}"; exit 1 ;;
esac
