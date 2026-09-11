# Relay engine (opaque store-and-repeat)

## 1. Purpose

Forward a transmission received on one channel to all other enabled channels.
User traffic is **opaque**: the relay never decodes/decrypts it. A transmission
is detected by RF energy, captured as an I/Q clip, stored, and replayed on the
destination channels.

```
capture on F1 -> relay -> replay on F2, F3, F4   (each independently)
capture on F2 -> relay -> replay on F1, F3, F4
...
```

## 2. Components

* `relay/engine.py` — routing, durable message creation, replay scheduling.
* `relay/link_monitor.py` — per-channel RF-energy state.
* `store/queue_db.py` — durable clips + metadata.
* `radio/manager.py` — time-division capture/replay.

## 3. Routing / channel management

* Routing source = the **physical channel** the burst was captured on.
* A captured clip fans out to **every other enabled channel**.
* Each destination gets an independent durable message (own status, retry and
  delivery), satisfying "independent state for all four channels".
* Per-destination clips are stored separately (one copy per destination).

### Loop / self-repeat control

No origin token can be read out of an opaque signal, so loop control uses:
1. strict RX/TX time separation (the manager never captures while replaying),
2. a per-channel receive **blackout** for `post_tx_guard_s` after a replay so
   the relay does not hear and re-repeat its own just-sent clip,
3. a recent-clip dedupe on (channel, start-time), and
4. physical antenna/isolation design.

This is the same, necessarily approximate, control that real transparent
cross-band repeaters use. Multi-node ring topologies where two relays hear each
other's repeats can still oscillate; that is documented as a topology
limitation, not silently claimed to be solved.

## 4. Deliverability

A destination is **open for delivery** when all of:
1. `radio.tx_enable` is true,
2. the TX device is present (`tx_dev_present()`),
3. the channel is enabled and `tx_allowed` is true,
4. in **relay mode**, the far relay node is currently heard (link state).

Anything closing a destination (no TX device, `tx_allowed=false`, channel
disabled, far end silent in relay mode) makes its deliveries **queue** and
retry until it opens — a real, hardware-honest demonstration of
store-and-forward. `relay.mode` = `bridge` (default) or `relay`.

## 5. Delivery confirmation

Opaque traffic has no payload ACK. A successful RF replay (`rf_tx`) is the
terminal "delivered" event. `relay.confirm` is reported as `rf_tx` for opaque
traffic. (Our own optional control channel conveys node-presence only, never
payload ACKs.)

## 6. Example: F4 unavailable then recovers

```
capture F1 clip -> stored; F2/F3 replayed & delivered; F4 queued (tx_allowed off)
operator re-enables TX on F4 (or far F4 node heard in relay mode)
relay loop sees F4 deliverable -> claims queued F4 clip -> replays -> delivered
```

## 7. Safety / failure handling

* Replays are bounded: retries capped, backoff grows, TTL expires clips,
  oversized clips dropped.
* The queue is durable (SQLite); a relay-engine crash does not lose queued
  clips and they flush on restart.
