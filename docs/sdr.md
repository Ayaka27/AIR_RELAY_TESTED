# SDR: device configuration, RX/TX paths, opaque capture, limitations

## 1. Devices and roles

| Backend | File | Role |
|---|---|---|
| RTL-SDR | `radio/rtl.py` | Capture/RX path: squelch-gated opaque I/Q burst capture |
| HackRF One | `radio/hackrf.py` | TX path: replays captured I/Q unchanged |
| NullDevice | `radio/sdr.py` | Standard interface, no RF; software bring-up only; never synthesizes inbound RF |

Selection follows `config/config.json`: `radio.rx_device` (`auto`|`rtl`|`none`)
and `radio.tx_device` (`auto`|`hackrf`|`none`). If a library/device is missing
at startup the manager logs it and uses a `NullDevice` (reported as "no
device" in the dashboard). The system never pretends a missing radio exists.

The relay never decodes/decrypts user traffic: the SDR layer only (a) detects
RF energy, (b) captures the burst as raw complex-baseband I/Q, (c) replays it.

## 2. Rate configuration

* `radio.rf_sample_rate_hz` (default 2,000,000): RF I/Q rate used for both
  capture (RTL) and replay (HackRF). ≥ ~2 MS/s is required by the HackRF, and
  this captured bandwidth (± ~1 MHz) is far wider than one handheld channel,
  so clips replay directly with no resampling.
* `radio.rx_gain_db` (0 = auto): RTL gain; keep the minimum that still hears
  the intended signal to limit desense.
* `radio.tx_amp_enable` / `radio.txvga_gain_db`: HackRF TX level settings.

## 3. Capture / replay pipeline

### Capture (RTL-SDR squelch-gated, no demod)

```
tune RTL to a channel centre
read IQ blocks
for each block: measure RF power
  power >= squelch.open_dbm  -> burst active: append block, reset silence
  power <  squelch.open_dbm  -> append tail; end burst after capture.tail_s
if no burst within capture.listen_s -> move to next channel
if burst ends (silence) or reaches capture.max_clip_s -> return opaque RxClip
```

`RxClip` carries: source channel, start time, centre frequency, sample rate,
duration, peak level, and the raw I/Q bytes. Content is never inspected.

### Replay (HackRF)

```
relay enqueues a TxJob(dst_channel, clip.iq, dst_freq)
manager tunes HackRF to dst_freq and transmits the clip bytes unchanged
on completion -> relay marks the message delivered (rf_tx)
```

## 4. Squelch (energy detection)

* `radio.squelch.enable` — gate burst capture.
* `radio.squelch.open_dbm` (default -95) — power that opens a capture.
* `radio.squelch.hold_dbm` — close hysteresis (kept for tuning).
* `radio.capture.listen_s` — how long to dwell per channel scanning.
* `radio.capture.max_clip_s` — clip length cap.
* `radio.capture.tail_s` — silence that ends a burst.
* `radio.capture.post_tx_guard_s` — after replaying on a channel, ignore that
  channel for this long so the relay does not re-capture its own clip.

These are the ONLY parameters used on the user signal (plus RF power
measurement). There is deliberately no demodulator, decoder, or modem on the
user path.

## 5. RSSI / level

RX level is measured from RF power (`radio/rtl.py: _block_power_dbm`), a
relative S-meter (not a calibrated dBm). It drives squelch, link state and the
altitude scorer. Absolute calibration is a field task (antenna factor + device
gain).

## 6. Limitations (must read)

* A single RTL-SDR captures ONE channel at a time; RX is a time-division scan.
  A transmission on another channel while the relay is listening/capturing on
  the current channel is not captured (fundamental single-RX limit).
* RX and TX cannot overlap; the scheduler alternates them.
* No payload-level ACK or decoding: delivery = successful RF replay.
* Loop control is by time separation + post-TX receive blackout + physical
  isolation; it is not a hard guarantee against a second relay in RF range
  re-repeating (transparent-repeater limitation).
* Capture storage is significant (≈8 MB/s at 2 MS/s). Bound `max_clip_s`;
  a long burst is cut off at the cap.
* `hackrf.py` tolerates the two common python-hackrf API shapes; confirm against
  the installed binding during hardware bring-up.

## 7. Self-interference

Handled by strict RX/TX time separation in the scheduler plus the RF hardware
in `rf_architecture.md`/`bom.md`. Software filtering is an addition, never a
substitute.
