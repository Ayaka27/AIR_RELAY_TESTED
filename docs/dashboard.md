# Wireless dashboard

## 1. Access

The dashboard is served by Flask on the Pi, bound to `0.0.0.0:8080`
(`dashboard.host/port`). A ground laptop/tablet/phone opens:

```
http://<pi-address>:8080
```

over the authorized control link (Wi-Fi client join or private AP, see
`scripts/setup_network.sh`). No physical monitor is needed on the Pi.

## 2. Pages / panels (single page)

* **Radio configuration** — per channel: MHz entry, TX allowed, enabled.
  `Apply Configuration` validates → persists → pushes the new frequency set to
  the radio subsystem → confirms → logs the change → updates state.
* **Link monitor** — state, freq, RSSI, SNR, last Rx, queue, OK/fail per channel.
* **Relay events** — live log of receive/enqueue/transmit/deliver/retry events.
* **Store & forward queue** — message ID, F→F, state, age, retry, length;
  clear expired/failed control.
* **Drone / Pixhawk** — altitude, position, mode, battery, armed, health.
* **Altitude optimization** — state, current vs suggested altitude, score.
* **System health** — CPU, RAM, temp, storage, RX/TX device presence, service
  states, config revision.

Real-time: the page polls `/api/overview` every second; live events arrive via
Server-Sent Events on `/api/stream`.

## 3. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/overview` | aggregate snapshot |
| GET | `/api/config/channels` | current channels + band limits |
| PUT | `/api/config/channels` | apply new channel frequencies |
| GET/PUT | `/api/config` | read config / update allow-listed runtime params |
| GET | `/api/messages` | queue view + stats |
| POST | `/api/queue/clear_expired_failed` | remove terminal entries |
| POST | `/api/queue/purge_delivered` | drop delivered history |
| GET | `/api/links`, `/api/system`, `/api/radio`, `/api/flight`, `/api/altitude` | focused snapshots |
| POST | `/api/login`, `/api/logout`, GET `/api/auth_status` | auth |
| GET | `/api/stream` | SSE live events |
| POST | `/api/test/inject` | inject a test frame (only when `dev.allow_inject=true`) |

All mutation endpoints validate input and return `400` on invalid config
(out-of-band frequency, duplicate enabled frequency, malformed body), so the
system can never be left in a partially reconfigured state.

## 4. Configuration safety

Frequency changes are validated against the band limits and duplicate rules
*before* any apply, written atomically to `config.json`, then pushed to the
radio manager (`reconfigure`) and link monitor (`reconfigure`). The dashboard
shows the active configuration and the latest revision. Invalid submissions are
rejected and shown inline.

## 5. Security

* `dashboard.auth_enabled=false` by default for bench operation. Set `true` in
  production-style operation.
* When enabled, an operator **token** (generated once at first run and printed
  to the service log) is required. It is stored as a salted hash in
  `config/secrets.json`, never in the repository. Sessions use the token.
* Reads (`public_link_read`) can be left open while writes require auth.
* Malformed requests are rejected; unknown scalar config keys are ignored.
* Dashboard failure never affects the flight controller or the relay's core
  safety (no command path to the autopilot; queue is durable on disk).
