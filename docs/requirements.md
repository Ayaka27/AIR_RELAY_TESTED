# Requirements

## 1. Functional requirements

1. The relay bridges four radio channels F1–F4. A transmission received on any
   channel is forwarded to all other enabled channels.
2. F1–F4 are **logical** channel names. Their physical frequencies are operator
   configurable (MHz entry) and are **never** hard-coded in source.
3. Frequency configuration is centralized: the same channel set drives the
   dashboard, backend, relay engine, radio manager and SDR devices.
4. Relay is **store-and-forward**: received communication is persisted, then
   transmitted to currently available destinations; unavailable destinations
   are queued and retried when they become available.
5. Queued messages survive communication loss **and** application restarts
   (durable on-disk queue).
6. Continuous per-channel link monitoring with derived states
   (UNKNOWN/SEARCHING/AVAILABLE/ACTIVE/DEGRADED/UNAVAILABLE/RECOVERING/DISABLED)
   and metrics (RSSI, SNR, activity, timestamps, queue length, delivery counts).
7. No uncontrolled/unbounded re-transmission: bounded retries, exponential
   backoff, message expiry, duplicate/loop protection.
8. Optional Pixhawk telemetry (altitude, position, mode, battery, health).
9. Optional altitude optimization that produces an advisory altitude suggestion
   (never commands the flight controller directly) with hysteresis and
   stabilization to avoid oscillation.
10. Headless operation with a browser dashboard providing: frequency
    configuration, link monitor, relay monitor, store-and-forward queue view,
    drone telemetry, altitude optimization and system health.

## 2. Hardware requirements

* Raspberry Pi 4 (Model B), 2/4/8 GB. See resource budget in `hardware.md`.
* RTL-SDR (RX/scan path) — V3 preferred for broader HF/VHF coverage.
* HackRF One (TX path) + external power amplifier for reachable handheld TX.
* Four FM handheld radios + their antennas (the test "channels").
* RF filtering/isolation hardware (see `rf_architecture.md`, `bom.md`).
* Pixhawk flight controller and telemetry/serial link (optional).
* Antennas for the relay RX and TX, plus separation.
* Ground device (laptop/tablet/phone) with Wi-Fi.
* A power source sized for Pi + SDRs + PA + flight hardware.

## 3. Software requirements

* Raspberry Pi OS (64-bit recommended), Bullseye/Bookworm.
* Python 3.9+.
* `librtlsdr`, `libhackrf`, python bindings (`pyrtlsdr`, `hackrf`).
* `flask` (project dep; `numpy` optional, no longer required by the RF path).
* `pymavlink` (optional, flight).
* systemd for the service; hostapd/dnsmasq or wpa_supplicant for the control
  link.

## 4. Constraints

* **One receive path, one half-duplex transmit path.** RTL-SDR can listen on
  one frequency at a time; HackRF can transmit on one frequency at a time and
  cannot transmit while the relay needs to receive. => RX scanning and TX are
  **time-shared** (single scheduler thread). No four-channel simultaneous
  receive or full-duplex operation is claimed.
* RTL-SDR tuning range ~24 MHz–1.7 GHz; usable sensitivity varies by band.
* HackRF TX output is low power (~–10..0 dBm typical before amp); an external
  PA + filtering is required for reach.
* A single RTL-SDR RX path is shared by four channels, so instantaneous
  reception on all four is impossible; this is a fundamental architecture
  limit of the chosen hardware and is documented, not hidden.
* Handheld signals are their own modulation and are opaque (and may be
  encrypted) to the relay; the relay never decodes them and forwards only as
  raw RF energy (see Assumptions).
* Regulatory: transmitting requires appropriate authorization; device
  certification and duty-cycle limits apply.

## 5. Assumptions

* The operator is an authorized/licensed user testing on permitted frequencies.
* Ground control reaches the Pi over an authorized wireless network.
* The four handhelds use their own modulation and encryption/scrambling, which
  is **out of scope**: the relay never decodes/decrypts them.
* The relay therefore forwards user traffic as **opaque RF energy**: detect →
  capture as raw I/Q → replay unchanged on destination channels.
* A captured burst fits within the RTL-SDR's capture bandwidth (≥ ~±1 MHz at the
  default rate) and its duration fits within `radio.capture.max_clip_s`.
* Because traffic is opaque, "delivered" means a successful RF replay, and loop
  control relies on time separation + receive blackout + physical isolation
  rather than an in-band token.
* Relay-node presence (for relay mode) is inferred by hearing the far relay
  node's traffic on the user channels, or optionally via a dedicated control
  channel that requires an extra receive device.
