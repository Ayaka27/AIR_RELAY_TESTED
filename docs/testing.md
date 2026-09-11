# Hardware testing

> Rule: a test passes only when the observed hardware/system behaviour meets the
> expected result — never merely because code executed. Record actual results in
> the log/tables below during each test session. Fill per-test: Date, Operator,
> Setup, Pass/Fail, Observed, Notes.

## 0. Test harness & bench

* Two+ AirRelay-capable nodes OR one AirRelay relay plus handheld FM radios fed
  and/or reference RF sources that transmit known bursts on the user channels.
* Recommended bench: relay node + RTL-SDR + HackRF(+PA), four handheld radios,
  RX/TX isolation hardware, a spectrum analyzer where available, and a
  controllable attenuator for the RX input.
* Always note frequency plan, distances, antenna/attenuator settings.

## 1. SDR bring-up tests

| Test | Procedure | Expected | Actual |
|---|---|---|---|
| RTL detection | `rtl_test` | device found, tuning OK | |
| HackRF detection | `hackrf_info` | board/firmware shown | |
| RX path | `airrelay serve` with RTL present; dashboard shows RX HEALTHY | RX present | |
| TX path | HackRF present; dashboard shows TX HEALTHY | TX present | |
| Frequency config | set channel freq via dashboard; read `radio.device_status`/logs | freq applied to device | |
| Gain | change `rx_gain_db` | sensitivity changes as expected | |
| RF isolation | transmit while observing RX on other channels | no desense when time-separated | |
| Self-interference | TX on F2 while RX on F1 with hardware isolation | capture on other channels unaffected per budget | |

## 2. Relay routing tests (all pairs)

For each source S in F1..F4, transmit a frame on S and confirm fan-out to the
other three and **not** back to S (loop prevention).

| Source | F1 | F2 | F3 | F4 |
|---|---|---|---|---|
| Expected recipients | — | ✓ | ✓ | ✓ |
| Observed (list) | | | | |

Confirm the messages appear `DELIVERED` (bridge) and no duplicate forwarding
occurs when the same frame is heard again.

## 3. Store-and-forward tests

| Test | Procedure | Expected | Actual |
|---|---|---|---|
| Destination unavailable | disable `tx_allowed` on F4, send on F1 | F4 delivery QUEUED; others delivered | |
| Message queued | check queue view | F4 entry status QUEUED | |
| Destination recovery | re-enable F4 TX | queued F4 message flushes | |
| Delivered | confirm status DELIVERED | delivery counter increments | |
| Retry behaviour | keep TX failing (confirm=ack, no ACK) | retry_count grows, backoff grows | |
| Retry limit | exceed `max_retry_count` | message FAILED | |
| Expiry | set short `ttl_s`, leave queued | message EXPIRED | |
| Duplicate prevention | inject same token twice | only first routed | |
| Restart survival | send, stop service, start | queued message still present | |

## 4. Link monitoring tests

| Scenario | Expected link state | Actual |
|---|---|---|
| No signal yet | SEARCHING | |
| Carrier/frame heard | ACTIVE then AVAILABLE | |
| Signal stops < degraded_timeout | DEGRADED | |
| Signal stops > degraded_timeout | UNAVAILABLE | |
| Signal returns | RECOVERING/ACTIVE | |
| Channel disabled | DISABLED | |

## 5. Pixhawk tests

| Test | Expected | Actual |
|---|---|---|
| Connection (flight.enabled) | telemetry present | |
| Altitude data | alt updated | |
| Mode/battery/health | fields populated | |
| Link loss | interface disconnected, optimizer holds | |
| Dashboard never commands | no MAVLink command sent (verified on GCS) | |

## 6. Altitude optimization tests

* Feasible + poor link → climb suggestion after stabilization.
* Strong link + high altitude → optional descent; otherwise hold.
* Stale telemetry → RECOVERY/hold.
* Manual (disabled) → MANUAL.
* Confirm no oscillation across many evaluation cycles.

## 7. Dashboard tests

| Test | Expected | Actual |
|---|---|---|
| Wireless access | page loads from ground device | |
| Frequency config | apply → applied, revision bumps, devices updated | |
| Invalid config | rejected with message, state unchanged | |
| Real-time telemetry | updates ~1 s | |
| Link/queue/system views | data shown | |
| Auth (if enabled) | read open / write requires token | |

## 8. Results log

Append every session here (a template `data/test_results.md` may be created and
updated on the Pi):

```
--- SESSION ---
Date:            Operator:
Frequency plan:  F1= F2= F3= F4=
Hardware notes:  distances / antennas / PA / attenuators
---
Test: <name>            Result: PASS/FAIL
Expected: <...>         Observed: <...>
Action taken / notes: <...>
```

## 9. Resource measurement

During a representative relay run record `scripts/control.sh diagnose` plus the
dashboard System health: CPU %, RAM %, temperature, storage, and note which
component is the bottleneck. Add observations to `hardware.md`/`changelog.md`.
