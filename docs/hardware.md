# Hardware

## 1. Raspberry Pi 4

* Onboard processing: RF-energy capture/replay, relay engine, store-and-forward, link
  monitoring, Pixhawk I/O, altitude optimizer, web server, logging, config.
* Operates **headless**. No monitor is required.
* Recommended: Raspberry Pi 4 Model B, 4 GB (8 GB for headroom), 64-bit OS,
  powered via a regulated 5 V/3 A supply separate from the RF/PA supply to
  avoid ground bounce.
* A small fan/heatsink is recommended: sustained DSP + PA + Pi is thermal
  heavy. See temperature notes in `troubleshooting.md`.

### Resource budget (expected, to be confirmed by testing)

| Resource | Expected use | Notes |
|---|---|---|
| CPU | Low–medium | Squelch energy detection + block streaming at 2 MS/s during capture only; the relay performs no demod/decoding of user traffic. |
| RAM | Low–medium | Buffer per dwell ~ a few MB; Python + numpy + Flask well within 4 GB. |
| USB | Critical | RTL-SDR + HackRF must have a **dedicated USB 2.0 bus**; the Pi 4 has separate USB 3.0 and USB 2.0 controllers. Do **not** hang both SDRs with a high-bandwidth device on the same hub/controller. |
| Storage | Low | SQLite queue + JSONL logs grow modestly; logs rotate. |
| Thermal | Monitor | Keep < 80 °C under load. |
| Power | Budget | Pi 4 (~0.6–1 A), RTL-SDR (~0.3 A), HackRF (~0.3 A, more on TX). The PA has its own supply. |

## 2. RTL-SDR (RX)

* Tunes ~24 MHz–1.7 GHz; single frequency at a time.
* V3 recommended for better HF/VHF sensitivity.
* Requires the DVB kernel driver to be blacklisted (installer does this) and
  a udev rule so it is accessible without root.
* Provide clean, filtered power; poor USB power degrades reception.

## 3. HackRF One (TX)

* Half-duplex wideband SDR; used for TX.
* **Low output power.** An external power amplifier is required to reach
  handheld radios at distance, plus TX filtering for harmonics/noise.
* Requires udev rule (installer) and clean power.

## 4. Antennas

* **RX antenna:** appropriate for the active RX band(s). For multi-band relay
  use a wideband or band-switched antenna (or a single well-placed antenna for
  the dominant band during a given test). Separated from TX antenna.
* **TX antenna:** matched to TX band, sized for PA output, placed for maximum
  isolation from the RX antenna.
* Keep cables short, use quality connectors, maintain a common ground.

## 5. RF filtering / isolation (required)

See `rf_architecture.md` §2 for the reasoning. At minimum the test bench
should include: an RX band-pass/notch filter on the TX frequencies, a TX
low-pass/band-pass filter on the PA output, attenuators for bench testing, and
adequate antenna/cable separation. Full list in `bom.md`.

## 6. Pixhawk

* Any Pixhawk-family autopilot with MAVLink.
* Connected over a USB/FTDI telemetry adapter or a telemetry radio, endpoint
  configured in `flight.connection` (default `udpin:127.0.0.1:14550` for a
  ground-side MAVProxy, or a serial port when onboard).
* The relay is a **pure telemetry consumer**; it never sends flight commands
  (see `pixhawk.md`).

## 7. Power

Provide separate, regulated supplies for the flight/Pi/PA domains and a common
ground. Avoid powering the PA from the Pi's USB bus.

## 8. Physical connections

```
[ RX antenna ]--[RX filter]--[ RTL-SDR ]--USB--[ Pi4 ]
[ TX antenna ]--[TX filter]--[ PA ]--[ HackRF ]--USB--[ Pi4 ]
                                        [ Pixhawk ]--UART/USB--[ Pi4 ]
```

See `architecture.md` (system/RF diagrams) for the graphical form.
