# Changelog

Format: Date | Change | Reason | Affected components | Testing | Result

| Date | Change | Reason | Affected | Testing | Result |
|---|---|---|---|---|---|
| 2026-09-09 | Initial build of AirRelay 1.0 | Fresh authorized prototype | All | Unit tests (`pytest`); software bring-up with NullDevice | Automated tests pass (software). Hardware tests pending on real SDR/radios |
| 2026-09-09 | Adopt time-division RX/TX architecture | Single RTL-SDR RX + single HackRF TX cannot be simultaneous/four-channel | `radio/manager.py`, docs | review + scheduler tests | — |
| 2026-09-09 | **Refactor to opaque store-and-repeat (no decoding)** | Handhelds use their own out-of-scope encryption/modulation; relay must not decode user traffic | `radio/*`, `relay/engine.py`, `store/queue_db.py`, `core/config.py`, `web/api.py`, tests | pytest suite + live inject check | passes (software). Hardware pending |
| 2026-09-09 | Removed FM/AFSK decoder/modem modules | User traffic is opaque; no demod/decoding on user path | removed `radio/baseband.py`, `radio/dsp.py` | compile + tests | passes |
| 2026-09-09 | SQLite schema migration for clip metadata | Existing queue DB upgrades in place across model change | `store/queue_db.py` | migration test | passes |

> Append new rows as hardware tests proceed. Never mark a hardware test PASSED
> unless the physical result meets the expectation.

## Architecture decisions log

* **Opaque no-decode forwarding.** The relay detects RF energy, captures the
  burst as raw I/Q, and replays it unchanged on destination channels. It never
  demodulates, decodes, or decrypts user traffic (modulation/encryption-agnostic
  "bent pipe"). Delivery = successful RF replay; loop control = time separation
  + post-TX receive blackout + physical isolation (transparent-repeater
  limitation, documented not claimed solved).
* **Channel bridge vs store-and-forward.** "Unavailable destination" is modelled
  as an open/closed delivery window per channel (TX present + enabled +
  tx_allowed [+ far end heard in relay mode]); closing it queues deliveries.
* **Control plane separate.** Any relay-node signalling is the relay's own data
  and never rides on user channels; a dedicated control frequency would need an
  extra receive device, so presence is inferred by hearing far-node traffic on
  the user channels in the default config.
* **Altitude optimizer is advisory** and never commands the autopilot, keeping
  the Pixhawk-safety guarantee simple and auditable.
