#!/usr/bin/env bash
# AirRelay automated installer for a fresh Raspberry Pi 4.
#
#   1) system packages (SDR, HackRF, build, git, python)
#   2) python venv + project dependencies
#   3) RTL-SDR & HackRF udev rules + blacklist DVB kernel module
#   4) (optional) Pixhawk serial permissions
#   5) network configuration for the control link
#   6) systemd service registration
#
# Target installation is /opt/airrelay. Run from the project directory with:
#     sudo bash scripts/install_raspberrypi.sh
#
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/airrelay"
VENV="$INSTALL_DIR/venv"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"; exit 1
fi

echo "==> AirRelay installer"

if [[ "$PROJECT" != "$INSTALL_DIR" ]]; then
  echo "==> Copying project from $PROJECT to $INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  rsync -a --delete \
      --exclude venv --exclude data --exclude logs --exclude __pycache__ \
      --exclude '*.pyc' --exclude .git \
      "$PROJECT"/ "$INSTALL_DIR"/
  PROJECT="$INSTALL_DIR"
  cd "$PROJECT"
fi

echo "==> Installing system packages"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip python3-dev python3-numpy python3-flask \
  build-essential git rsync cmake \
  librtlsdr-dev rtl-sdr \
  libhackrf-dev hackrf \
  libusb-1.0-0-dev \
  sqlite3 \
  rfkill iw wireless-tools hostapd dnsmasq 2>/dev/null || true

echo "==> Creating python virtualenv"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel setuptools
"$VENV/bin/pip" install -r "$PROJECT/requirements.txt"

echo "==> RTL-SDR / HackRF udev rules"
cat > /etc/udev/rules.d/20-rtlsdr.rules <<'RULES'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
RULES
cat > /etc/udev/rules.d/21-hackrf.rules <<'RULES'
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6089", MODE="0666"
RULES

# Prevent the kernel DVB driver from claiming the RTL-SDR.
if grep -q "dvb_usb_rtl28xxu" /etc/modprobe.d/blacklist-rtlsdr.conf 2>/dev/null; then
  :
else
  echo "blacklist dvb_usb_rtl28xxu" > /etc/modprobe.d/blacklist-rtlsdr.conf
  echo "blacklist rtl2832"        >> /etc/modprobe.d/blacklist-rtlsdr.conf
  echo "blacklist rtl2830"        >> /etc/modprobe.d/blacklist-rtlsdr.conf
fi

echo "==> Pixhawk serial permissions (USB FTDI / telemetry)"
cat > /etc/udev/rules.d/99-pixhawk.rules <<'RULES'
SUBSYSTEM=="tty", ATTRS{idVendor}=="26ac", MODE="0666"
KERNEL=="ttyACM*", MODE="0666", GROUP="dialout"
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout"
RULES
usermod -aG dialout root 2>/dev/null || true

echo "==> (optional) Network configuration"
if [[ -f "$PROJECT/scripts/setup_network.sh" ]]; then
  echo "Run scripts/setup_network.sh separately to bring up the control link."
fi

echo "==> Registering systemd service"
bash "$PROJECT/scripts/install_service.sh"

udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

echo ""
echo "INSTALL COMPLETE."
echo "  1. Connect RTL-SDR and HackRF.  Verify:  rtl_test  /  hackrf_info"
echo "  2. Bring up the control link:   scripts/setup_network.sh"
echo "  3. Start the service:           scripts/control.sh start"
echo "  4. Open the dashboard at the Pi's address, port $(grep -o '\"port\": [0-9]*' config/config.json | head -1 | grep -o '[0-9]*' | head -1 || echo 8080)"
