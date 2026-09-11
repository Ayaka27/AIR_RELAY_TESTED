# Installation (Raspberry Pi 4)

## 1. Prerequisites

* Raspberry Pi 4 with Raspberry Pi OS 64-bit (Bullseye/Bookworm).
* Internet access for `apt`/`pip` during install.
* The project present on the Pi (e.g. `/opt/airrelay`).

## 2. Automated install

```bash
cd /opt/airrelay
sudo bash scripts/install_raspberrypi.sh
```

This performs:

1. System packages: python3-venv, build tools, `librtlsdr-dev rtl-sdr`,
   `libhackrf-dev hackrf`, libusb, sqlite3, network tools (hostapd/dnsmasq).
2. A Python virtualenv at `venv/` and `pip install -r requirements.txt`
   (flask, numpy, pyrtlsdr, hackrf, pymavlink).
3. udev rules for RTL-SDR (0bda:2838/2832) and HackRF (1d50:6089) with mode 0666.
4. Kernel blacklist for the DVB RTL driver (`dvb_usb_rtl28xxu` etc.) so the
   dongle is exposed to librtlsdr.
5. Pixhawk serial permissions (dialout, ttyACM/ttyUSB 0666).
6. Systemd service registration via `scripts/install_service.sh`.

Reboot once after install (or `udevadm trigger`) so blacklist/rules apply.

## 3. Verify hardware

```bash
sudo bash scripts/diag_hardware.sh
# RTL-SDR:  rtl_test -t  -> should print "No devices found" only if missing, or tuning ok
# HackRF:   hackrf_info -> board id / firmware / serial
```

## 4. Bring up the control link

```bash
# Join an authorized Wi-Fi network:
sudo bash scripts/setup_network.sh client "SSID" "passphrase"
# OR create an AP:
sudo bash scripts/setup_network.sh ap AirRelay-AP 'passphrase>=8chars'
```

Note the Pi's IP (`hostname -I`). The dashboard binds `0.0.0.0:8080`.

## 5. Service control

```bash
bash scripts/control.sh start      # start + show recent logs
bash scripts/control.sh status
bash scripts/control.sh logs       # follow
bash scripts/control.sh restart
bash scripts/control.sh stop
bash scripts/control.sh diagnose   # hardware + app diagnostics
```

The service starts automatically on boot (`systemctl enable`).

## 6. First-run configuration

On first start the service creates `config/config.json` (defaults) and, if
auth is enabled, prints a one-time operator token to the log
(`scripts/control.sh logs`). Open the dashboard, enter the four operating MHz
frequencies, click Apply.

## 7. Optional: separate console vs service

For bring-up you can run in the foreground from the project dir:

```bash
# from /opt/airrelay, with venv:
venv/bin/python -m airrelay serve
```

(Running the service via systemd is the normal operating mode.)

## 8. Common failure points

* RTL-SDR not usable → DVB blacklist not applied / rules not reloaded
  (`dmesg | grep -i dvb`). See `troubleshooting.md`.
* No dashboard → service not started / wrong network / firewall.
* HackRF TX fails → installed binding differs (see `sdr.md` §6).
