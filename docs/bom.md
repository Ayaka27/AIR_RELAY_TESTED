# Bill of materials

> Quantities assume a single relay node + four-channel test bench. "Mandatory"
> = required for correct RF isolation / operation of the documented architecture.
> Verify specifications against your exact radios/bands before purchase.

| # | Component | Qty | Purpose | Required spec | Interface | Req? | Reason |
|---|---|---|---|---|---|---|---|
| 1 | Raspberry Pi 4 Model B | 1 | Onboard processor / host | 4 GB+ RAM, 64-bit OS, 5 V/3 A PSU | USB, Ethernet, GPIO | Mandatory | Relay brain |
| 2 | RTL-SDR V3 | 1 | RX / channel-scan path | 24 MHz–1.7 GHz, RTL2832U+R820T2 | USB | Mandatory | Receive all channels (time-shared) |
| 3 | HackRF One | 1 | TX path | 1 MHz–6 GHz half duplex, ≥2 MS/s | USB | Mandatory | Transmit on configurable channels |
| 4 | External TX power amplifier (PA) | 1 | Boost HackRF to reach handhelds | TX-band gain +20–40 dB, linear, low harmonics | RF in/out, separate PSU | Mandatory* | HackRF alone too weak (*except very-near bench) |
| 5 | RX antenna | 1+ | Receive relay channels | Matched to active RX band(s) | SMA/N | Mandatory | Acquisition |
| 6 | TX antenna | 1 | Radiate relay TX | Matched to TX band, handled by PA | SMA/N | Mandatory | Transmission |
| 7 | RX band-pass / tunable notch filter | 1 | Reject TX-band & out-of-band energy at RX | Covers RX band; deep rejection at TX freqs | inline RF | Mandatory | Prevent desense/overload (self-interference) |
| 8 | TX low-pass / band-pass filter | 1 | Suppress PA harmonics & broadband noise in RX band | TX passband, rejection in RX band | inline RF | Mandatory | Prevent TX noise reaching RX |
| 9 | Antenna separation hardware (mast/mounts/ground plane) | 1 set | Physical isolation RX↔TX | ≥ several wavelengths, ground plane | mechanical | Mandatory | RF isolation |
| 10 | Coaxial jumpers & adapters | several | Hook antennas/filters/devices | 50 Ω, short, quality connectors | SMA/UHF | Mandatory | Clean RF path |
| 11 | Attenuators (10/20/30 dB) | 2 | Bench RX overload tests / level control | 50 Ω, band | inline | Recommended | Testing, calibration |
| 12 | RF shield / enclosure | 1 | Shielding + grounding of RX path | metal, grounded | mechanical | Recommended | Isolation |
| 13 | Ferrite beads / chokes | several | Suppress cable/common-mode coupling | on USB/power | clamp | Recommended | Isolation |
| 14 | Regulated 5 V Pi/SDR PSU | 1 | Pi + SDRs | 5 V/3 A low-ripple | USB-C | Mandatory | Stability |
| 15 | PA PSU | 1 | Power amplifier | PA-rated | DC | Mandatory* | TX reach |
| 16 | Pixhawk flight controller | 1 | Flight + telemetry + altitude | any Pixhawk-family | USB/serial | Optional | Altitude optimization / telemetry |
| 17 | Pixhawk telemetry/USB cable | 1 | Link Pixhawk↔Pi | matches controller | UART/USB | Optional | Telemetry |
| 18 | Drone platform + power | 1 | Carry payload airborne | payload capacity, flight time | — | Optional | Airborne ops |
| 19 | Wi-Fi adapter (Pi4 has built-in) | 1 | Control link | 802.11 b/g/n | USB/internal | Mandatory | Dashboard access |
| 20 | Ground control device | 1 | Operator dashboard | Wi-Fi + browser | — | Mandatory | Operation |
| 21 | Handheld FM radios (4) | 4 | The four channels F1–F4 | FM, VHF/UHF as configured | RF | Mandatory | Test channels |
| 22 | Reference transmit source(s) for F1–F4 | 2+ | Generate known RF bursts on the user channels for relay tests | matched to band/channel | RF | Recommended | Verify capture→replay without decoding |
| 23 | microSD card (32 GB+) | 1 | OS + data | class 10 / A2 | microSD | Mandatory | Boot/storage |
| 24 | Heatsink + fan (Pi) | 1 | Thermal management | fits Pi4 | mechanical | Recommended | Sustained DSP |

### Mandatory isolation summary

Items 7, 8, 9 (filters + physical separation) are mandatory to satisfy the
"RTL-SDR must not be desensed/overloaded by the HackRF" requirement, because
software filtering alone cannot fix physical RF self-interference. Items 2–4 +
7 + 8 enable the actual time-division TX/RX architecture.
