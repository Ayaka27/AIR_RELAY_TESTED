# Troubleshooting

Practical procedures. First stop: `bash scripts/control.sh diagnose` and
`scripts/control.sh logs`.

## RTL-SDR not detected

```
rtl_test          # should find a device
dmesg | tail -30  # dvb_usb_rtl28xxu claiming the dongle?
```
* Ensure DVB driver blacklist applied (`grep -r rtl28xxu /etc/modprobe.d/`) and
  reboot after install.
* Ensure udev rule present and dongle re-plugged.
* Try a different USB port / powered hub; RTL-SDRs are power-hungry.
* Two RTL dongles share one serial when some RTL2832U clones — use a real
  RTL-SDR V3 with unique serial or set `rtl_tcp`.

## HackRF not detected

```
hackrf_info
lsusb | grep 1d50:6089
```
* udev rule missing → re-run installer, `udevadm control --reload-rules`.
* Binding mismatch → confirm `python -c "import hackrf"`; adapt `hackrf.py`
  `tx_iq` to the installed API (documented in `sdr.md`).

## No RF signal / no bursts captured

* Frequency plan mismatch between dashboard and the reference source/handheld.
* FM deviation / audio levels mismatched (`radio.tx_fm_deviation_hz`).
* RTL gain too low/auto squelch; increase or set manual.
* Antenna/cable fault. Verify with a known signal on a spectrum/`rtl_power`.
* Confirm the source is actually transmitting; check squelch threshold and listen levels.

## RF overload / desense

* Ensure the scheduler is truly time-separating RX and TX (only one action per
  loop iteration). Confirm in logs there are no concurrent RX+TX.
* Add/verify RX band-pass/notch filter and TX filter; increase antenna
  separation; reduce TX power/PA; lower RTL gain.
* Software filtering alone is not sufficient — see `rf_architecture.md`.

## SDR conflicts / USB issues

* Both SDRs on the same bus throttling? Put RTL on USB 2.0 and HackRF on USB
  3.0 controllers of the Pi 4.
* CPU high → reduce `rx_sample_rate_hz`/`rx_dwell_ms`, or fewer channels.
* USB resets → check `dmesg`, power.

## Dashboard unavailable

* Service running? `bash scripts/control.sh status`.
* Network: `hostname -I`; reachable from ground device? Firewall on port 8080.
* If auth enabled and you lost the token, delete `config/secrets.json` and
  restart to regenerate a token (print once to log).

## Pixhawk unavailable

* `flight.enabled=true` and endpoint correct.
* Serial permissions (dialout) applied + reboot.
* Confirm on a GCS (Mission Planner/QGC) the same endpoint works.
* Relay never commands; if nothing appears, check `flight.telemetry` in the API.

## Link monitoring failure

* No far-end beacons → in `bridge` mode links may sit at SEARCHING/UNAVAILABLE
  (expected; they only go ACTIVE while a signal is heard). This does not block
  bridge TX.
* Adjust `link_monitor` timeouts if transitions feel too slow/fast.

## Queue problems

* Messages stuck QUEUED → destination not open: TX device absent,
  `tx_allowed=false`, channel disabled, or relay-mode far end silent.
* Messages FAILED/EXPIRED too fast → raise `max_retry_count`/`ttl_s`.
* Queue huge → raise `max_queue_size` or clear terminal entries on the dashboard.

## High CPU

* Long RX dwell at high sample rate. Reduce `rx_sample_rate_hz` (keep ≥ HackRF
  needs only for TX; RX can use ~1–2 MS/s), reduce `rx_dwell_ms`, fewer enabled
  channels. Add heatsink/fan.

## High temperature

* Pi 4 throttles at ~80 °C. Add fan/heatsink, ensure airflow, reduce DSP load,
  and keep PA away from the Pi. Monitor temperature in System health.

## After any change

Rebuild config if needed, restart service, re-run the relevant section of
`docs/testing.md`, and record the result in `docs/changelog.md`.
