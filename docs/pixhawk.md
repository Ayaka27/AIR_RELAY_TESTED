# Pixhawk integration & altitude optimization

## 1. Pixhawk interface

* Uses **pymavlink** (`mavutil.mavlink_connection`) over the endpoint in
  `flight.connection` (default `udpin:127.0.0.1:14550`, or a serial device).
* Enabled only when `flight.enabled=true`.
* Reads: altitude (AMSL and relative), position (lat/lon), flight mode,
  armed state, battery voltage/%, GPS fix, vehicle health.

## 2. Safety policy (Pixhawk)

The relay is a **pure telemetry consumer**. It never sends flight commands
(`send_ground_cmd` is a documented no-op). Therefore:

* A dashboard/network failure **cannot** cause flight behaviour — there is no
  command path from the dashboard to the autopilot.
* Pixhawk connection loss → interface reports disconnected; altitude optimizer
  immediately holds.
* Invalid/stale telemetry → optimizer holds (freshness gate).
* Relay/SDR/dashboard crash → flight controller is unaffected (it only ever
  receives MAVLink, and does not depend on the relay).

## 3. Altitude optimizer (advisory)

`flight/altitude.py` runs a closed monitoring loop that observes link quality
and produces a **suggested altitude** for the human pilot or a guided ground
station. It never commands the vehicle.

### Inputs (weighted, not RSSI alone)

* reachable-channel fraction (coverage)
* mean RSSI (relative S-meter)
* SNR where available
* delivery success rate (stability)
* current altitude
* operational limits (min/max altitude)

### Score

```
score = w_coverage*coverage + w_rssi*rssi_norm + w_snr*snr_norm
        + w_stability*success - w_altitude_cost*alt_norm
```

### States

`MANUAL | MONITORING | OPTIMIZATION | HOLD | RECOVERY`.

* `MANUAL` — optimizer disabled (`altitude.enabled=false`).
* `RECOVERY` — telemetry stale/missing or altitude out of limits; **hold**.
* `MONITORING` — measuring; only acts after a **stabilization period**.
* `HOLD` — after issuing a suggestion, waits out a **cooldown** so the vehicle
  and RF environment settle (prevents oscillation).
* `OPTIMIZATION` — an explicit climb/descend suggestion is pending.

### Anti-oscillation

Hysteresis (min step = `altitude.hysteresis_m`), stabilization dwell
(`altitude.stabilization_s`), cooldown (`altitude.cooldown_s`), and discrete
step size (`altitude.alt_step_m`) together ensure the optimizer does not
continuously bounce between altitudes.

### Behaviour

* Poor persistent score + altitude headroom below max → suggest a **climb**
  (restore line-of-sight coverage for an airborne relay).
* Strong score and well above the floor → may suggest a conservative **descent**
  to save power.
* Otherwise → hold/monitor.
