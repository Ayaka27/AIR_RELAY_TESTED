# AirRelay — Airborne Multi-Band SDR Relay

A headless **Raspberry Pi 4** airborne relay station that bridges **four
configurable radio channels** using an **RTL-SDR** (receive/scan path) and a
**HackRF One** (half-duplex transmit path), with **persistent store-and-forward**,
continuous **link monitoring**, optional **Pixhawk** integration and automatic
**altitude optimization**, and a **wireless browser dashboard** for configuring
the four operating frequencies and monitoring the whole system.

> ⚠️ **Not a simulation and not a toy abstraction.** This is a real RF relay
> with real hardware constraints. The most important constraint — a single
> receive path and a single half-duplex transmit path cannot watch or use all
> four channels simultaneously — is handled by a **time-division scheduler**,
> never by pretending otherwise. See `docs/rf_architecture.md`.

> 🔒 **Opaque relay — the relay does NOT decode/decrypt user traffic.** The four
> handhelds use their own modulation and encryption, which is out of scope. The
> relay only **detects** RF energy, **captures** the active burst as raw I/Q,
> and **replays** it unchanged on the destination channels. "Delivered" means a
> successful RF replay; no in-band ACK is possible. See `docs/relay.md`.

```
F1 → Relay → F2     F2 → Relay → F1     ... any channel to all others
           → F3               → F3
           → F4               → F4
```

---

## Highlights

* **No hard-coded frequencies.** F1–F4 are logical names; the operator enters
  real MHz values in the dashboard. Centralized configuration drives the
  dashboard, backend, relay engine, radio manager and SDR alike.
* **Persistent store-and-forward.** Messages survive link loss **and**
  application restart (SQLite on disk), with retries, backoff, expiry, queue
  limits and delivery tracking.
* **Real hardware honesty.** Documented RF self-interference strategy (TX/RX
  separation, filters, isolation, TX-power budgeting) plus the single-Pi
  timing reality.
* **Optional Pixhawk** telemetry and advisory altitude optimization that never
  commands the flight controller.
* **Runs headless** behind a single systemd service; browser dashboard over the
  authorized control link.

---

## Quick start (Raspberry Pi 4)

```bash
# 1. Deploy the project to the Pi (copy this folder, e.g. to /opt/airrelay)
# 2. Run the automated installer:
sudo bash scripts/install_raspberrypi.sh

# 3. Bring up the control link (client join or private AP):
sudo bash scripts/setup_network.sh client "MyNetwork" "passphrase"   # or: ap AirRelay-AP passphrase1234

# 4. Start the service:
bash scripts/control.sh start          # status/logs/diagnose/restart/stop

# 5. Open the dashboard on a ground device:
#    http://<pi-address>:8080
```

See `docs/installation.md` for full details.

---

## Repository layout

```
airrelay/
├── src/airrelay/
│   ├── core/          config, data models, event bus
│   ├── radio/         SDR abstraction, RTL/HackRF backends, DSP, time-division manager
│   ├── relay/         link monitor, relay engine
│   ├── store/         persistent SQLite store-and-forward
│   ├── flight/        Pixhawk (MAVLink) interface, altitude optimizer
│   ├── web/           Flask app, REST/SSE API, context wiring, auth
│   └── util/          logger, paths, resource monitor
├── dashboard/         web UI (templates/static)
├── config/            config.json, secrets.json (generated)
├── data/              store.sqlite3
├── logs/              structured JSONL logs
├── scripts/           install / network / control / diagnostics
├── services/          systemd unit
├── tests/             pytest suite
└── docs/              full technical documentation
```

## Documentation index

| Document | Contents |
|---|---|
| `docs/requirements.md` | Functional, hardware, software requirements; constraints; assumptions |
| `docs/architecture.md` | System/RF/software architecture, data flow, diagrams (Mermaid) |
| `docs/rf_architecture.md` | SDR capabilities, RF self-interference mitigation, time-division design |
| `docs/hardware.md` | Raspberry Pi, RTL-SDR, HackRF, antennas, filters, Pixhawk, power, BOM link |
| `docs/bom.md` | Bill of materials |
| `docs/sdr.md` | Device config, RX/TX paths, processing pipeline, limitations |
| `docs/relay.md` | Routing, channel management, message lifecycle, link monitoring |
| `docs/store_forward.md` | Queue model, states, retry/backoff, expiry, durability |
| `docs/pixhawk.md` | Pixhawk connection, telemetry, altitude optimization, safety |
| `docs/dashboard.md` | Dashboard architecture, network access, config, API, real-time |
| `docs/installation.md` | Raspberry Pi setup, dependencies, SDR install, services |
| `docs/testing.md` | Hardware test procedures, expected vs actual results |
| `docs/troubleshooting.md` | Practical fault-finding |
| `docs/changelog.md` | Change management |

## Tests

```bash
cd airrelay
python3 -m pytest tests/
```

## Licensing

BSD-3-Clause. See `LICENSE`. **Regulatory notice:** transmitting on amateur or
land-mobile radio frequencies requires appropriate licensing and authorization
in your jurisdiction; operating a hackRF-based relay may also be subject to
device-certification and duty-cycle rules. This prototype is intended for
authorized testing only.
