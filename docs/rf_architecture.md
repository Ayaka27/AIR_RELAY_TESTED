# RF architecture & self-interference strategy

## 1. Hardware reality (analysed first, not assumed)

| Device | Role | Key limits |
|---|---|---|
| RTL-SDR | Receiver / scan path | Tunes **one** frequency at a time (~24 MHz–1.7 GHz); ~2.4 MS/s max; no TX; medium sensitivity. A single RTL-SDR cannot simultaneously cover four channels. |
| HackRF One | Transmit path | Half duplex, one frequency at a time; sample rate ≥ ~2 MS/s; **low output power** (~–10…0 dBm typical before its onboard amp). Cannot receive while the relay also needs clean RX on another path. |
| Raspberry Pi 4 | DSP + control | CPU/RAM/thermal budget limits how much SDR DSP can run; see `hardware.md`. |

**Consequences (explicit, not papered over):**

1. Only **one channel is received at a time** → RX is a **time-division scan**
   over enabled channels (round-robin dwells).
2. RX and TX **cannot overlap** (HackRF TX would desense/overload the RTL-SDR
   listening on a nearby channel) → TX bursts are inserted into the same
   scheduler between RX dwells.
3. The HackRF alone cannot reach handheld FM radios at useful range → an
   **external power amplifier + filtering** is required (see BOM).
4. Full-duplex simultaneous relay on the same radio is impossible with one
   RTL + one HackRF → handled by the single-thread scheduler.

These are real constraints of the stated hardware; the scheduler
(`radio/manager.py`) implements time-division operation.

## 2. Self-interference problem

While the HackRF transmits (say on F2), the RTL-SDR listening on F1 (or the
same F2 leak) can be:

* **desensed** (AGC driven down by strong TX leakage), or
* **overloaded** (front-end compression), or
* simply **blocked** if RX and TX were simultaneous.

### 2.1 Mitigations (combined; software alone is insufficient)

1. **Time separation (primary).** The scheduler never RXes while TXing. This
   is the strongest, cheapest mitigation and works on any band.
2. **Physical antenna separation + antenna isolation.** RX and TX antennas are
   separated (≥ several wavelengths preferred, ground plane between where
   possible) and oriented to maximise RX-to-TX isolation. Cables are kept
   short and routed away from each other.
3. **RX band-pass / notch filtering.** A filter before the RTL-SDR RX antenna
   rejects energy at the TX frequencies (and other out-of-band strong
   signals). Because relay channels are spread (e.g. VHF + UHF), the RX filter
   is chosen for the active RX band and/or a tunable notch on the TX channel.
4. **TX filtering.** A low-pass/band-pass filter on the HackRF+PA output
   suppresses harmonics and wideband noise that would otherwise fall into the
   RX band.
5. **TX power budgeting.** The PA is sized and gated so TX power reaching the
   RX antenna (isolation + filter rejection) stays well below the RTL-SDR's
   linear range.
6. **Gain control.** RTL gain is kept at the minimum that still hears the
   intended signal; high gain worsens desense.
7. **Shielding & grounding.** The Pi/SDR enclosure shields the RX path; common
   ground and ferrites on USB/data cables reduce coupling.
8. **Software filters** help only *after* the RF is clean; they cannot fix
   physical overload/desense. They are therefore an addition, not a substitute.

These required RF components are captured in `bom.md` (filters, isolators,
cables, attenuators, antennas, PA).

## 3. Modulation / signal-format decision (NO decoding)

The four test handhelds use their own modulation and their own
encryption/scrambling, which is **outside this project's scope**. The relay
therefore does **not** demodulate, decode, decrypt or otherwise interpret the
user traffic on F1–F4. It treats a user transmission purely as **RF energy to
be detected and replayed**.

The relay operates as an **opaque cross-band store-and-repeat**:

```
detect RF energy on channel S
  -> capture the active burst as raw complex-baseband I/Q (never decoded)
  -> store it with metadata (source, time, center freq, sample rate, duration)
  -> replay the SAME captured I/Q on destination channels
```

Because no decoding is performed, the relay is modulation- and
encryption-agnostic: analog FM, and proprietary/encrypted digital waveforms all
pass through untouched as long as they fit in the captured bandwidth. This is
the "bent pipe" model used by real transparent cross-band repeaters.

Consequences:
* The relay cannot read who sent a message, what it says, or an in-band ACK.
* "Delivered" can only mean "the clip was successfully replayed on the
  destination RF path" (simplex radios cannot ACK opaque traffic).
* Loop/self-repeat control cannot rely on an origin token inside the signal; it
  uses strict RX/TX time separation + a receive blackout after each replay +
  physical isolation. This is a documented topology limitation, not a guarantee
  (same as real transparent repeaters).

### Control plane (kept strictly separate)

Whatever signalling the relay needs (node presence) is the relay's OWN data and
never rides on the user channels. It is off by default
(`control.enabled`). Because a single RTL-SDR cannot simultaneously scan the
user channels and monitor a fifth control frequency, a dedicated control
channel requires an additional receive device (see BOM). In the default
configuration, relay-node "presence" is instead inferred by hearing the far
relay node's own traffic on the user channels (link monitor), which needs no
extra hardware and never decodes the far node's user content.


## 4. Channel frequency architecture

* Channels are logical F1–F4. Frequencies are configured centrally
  (`config/config.json`, editable from the dashboard) and are shared by every
  layer. Duplicate/conflicting frequencies between enabled channels and
  out-of-band values are rejected on apply.
* The RX scan order is `radio.scan_order`; dwell `radio.capture.listen_s`.
* TX replays a captured clip on the destination channel's configured frequency.

## 5. Capture bandwidth & fidelity

The relay captures at `radio.rf_sample_rate_hz` (default 2 MS/s → ~ ±1 MHz
captured bandwidth). This is much wider than any single handheld channel, so
an individual narrowband FM channel — or a single proprietary digital channel —
fits comfortably. Because the HackRF's minimum usable sample rate is ~2 MS/s,
the capture rate is fixed high enough that clips can be replayed directly
without resampling. Capturing many separate channels simultaneously is not
possible with one RTL-SDR; only the channel currently being listened to is
captured.

Storage reality: I/Q at 2 MS/s ≈ 8 MB/s. `radio.capture.max_clip_s` bounds each
clip (default 1.0 s ≈ 8 MB per clip) and each clip is stored once per
destination. Budget SD/eMMC write speed and storage accordingly; reduce the
clip length or sample rate (if the TX device supports it) for long sessions.

## 6. Time-division scheduler loop

```
loop
  if TX (replay) job pending and last action was capture:
      replay one queued I/Q clip on its destination channel
      -> blackout that channel for post_tx_guard_s (avoid self re-capture)
  else:
      squelch-capture on the next enabled channel; hand complete clips to relay
```

Alternating strictly means the RTL-SDR is always quiet during HackRF TX, which
is the core self-interference guarantee, and the per-channel blackout prevents
the relay from hearing and re-repeating its own just-sent clip.
