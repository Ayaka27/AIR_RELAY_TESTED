# Architecture

This document describes the complete system. All diagrams are Mermaid and
render on GitHub or any Mermaid-capable viewer.

## 1. System architecture

```mermaid
flowchart TB
    subgraph Airborne
        PIH["Raspberry Pi 4 (headless)\nairrelay.service"]
        RTL["RTL-SDR\nRX / channel-scan"]
        HACK["HackRF One\nTX path"]
        PAX["Power amp + filters\n(RF TX chain)"]
        PIX["Pixhawk flight controller"]
        ANT_RX["RX antenna(s)"]
        ANT_TX["TX antenna"]
    end
    subgraph Ground
        GW["Ground device\n(laptop/tablet/phone)"]
    end
    subgraph Radios
        R1["Handheld 1 (F1)"]
        R2["Handheld 2 (F2)"]
        R3["Handheld 3 (F3)"]
        R4["Handheld 4 (F4)"]
    end

    PIH -- "USB (baseband IQ)" --> RTL
    RTL -- "antenna" --> ANT_RX
    PIH -- "USB (baseband IQ)" --> HACK
    HACK --> PAX --> ANT_TX
    PIH <-- "MAVLink (serial/UDP)" --> PIX
    GW <-- "Wi-Fi control link (dashboard/API)" --> PIH
    R1 <-- "F1" --> ANT_RX
    R1 <-- "F1" --> ANT_TX
    R2 <-- "F2" --> ANT_RX
    R2 <-- "F2" --> ANT_TX
    R3 <-- "F3" --> ANT_RX
    R3 <-- "F3" --> ANT_TX
    R4 <-- "F4" --> ANT_RX
    R4 <-- "F4" --> ANT_TX
```

## 2. Software architecture

```mermaid
flowchart LR
    subgraph Web
        DASH["Browser dashboard"]
        API["Flask REST + SSE API"]
        AUTH["Auth"]
    end
    subgraph Core
        BUS["Event bus"]
        CFG["Config manager (persistent)"]
        LOG["Structured logger"]
    end
    subgraph Relay
        ENG["Relay engine"]
        LM["Link monitor"]
        SF["Store-and-forward (SQLite)"]
    end
    subgraph Radio
        MGR["Radio manager (time-division scheduler)"]
        CAP["Squelch-gated I/Q capture (no demod)"]
        SDRAB["SDR abstraction"]
        RTL["RTL-SDR backend"]
        HACK["HackRF backend"]
    end
    subgraph Flight
        PX["Pixhawk interface"]
        ALT["Altitude optimizer"]
    end

    DASH --> API --> AUTH
    API --> CFG
    API --> ENG
    DASH <-- "SSE" --> BUS
    ENG --> BUS
    MGR --> BUS
    PX --> BUS
    CFG --> ENG
    CFG --> MGR
    CFG --> LM
    ENG --> SF
    ENG --> LM
    MGR --> LM
    ENG --> MGR
    MGR --> CAP --> SDRAB
    SDRAB --> RTL
    SDRAB --> HACK
    PX --> PIX
    ALT --> LM
    ALT --> PX
```

The web server, radio manager, relay engine, store and (optionally) Pixhawk
interface run in **one process** under a single systemd unit. They communicate
via direct calls and the in-process event bus. This keeps service dependency
handling simple while still reporting each component's health independently.

## 3. RF architecture

```mermaid
flowchart LR
    subgraph RX_path
        ANT_RX --> FILT_RX["Bandpass/notch"] --> RTL --> MGR
    end
    subgraph TX_path
        MGR --> HACK
        HACK --> PA["Power amp"]
        PA --> TXFILT["Filter / duplexer / notch"] --> ANT_TX
    end
```

Key design rules (details in `rf_architecture.md`):

* RTL-SDR only **receives**; HackRF only **transmits** (relay-role).
* RX and TX are **never** simultaneous — the scheduler alternates them so the
  HackRF never desenses the RTL-SDR's current channel.
* Antennas are physically separated; the RX antenna carries a filter that
  rejects the TX band, and TX leakage power is budgeted below RTL desense
  thresholds. Software filtering alone is never relied on to solve RF
  self-interference.

## 4. Data flow (message)

```mermaid
sequenceDiagram
    participant H1 as Handheld on F1
    participant RTL as RTL-SDR
    participant MGR as Radio manager
    participant ENG as Relay engine
    participant SF as Store (SQLite)
    participant HACK as HackRF TX

    H1->>RTL: opaque RF burst on F1
    RTL->>MGR: captured I/Q clip @ F1 (no demod)
    MGR->>ENG: RxClip + rssi (rx_channel=F1)
    ENG->>SF: persist opaque clip(s) F1→F2,F3,F4 (QUEUED)
    ENG->>MGR: enqueue replay to F2
    MGR->>HACK: replay I/Q @ F2
    HACK-->>ENG: tx_done(ref, ok)
    ENG->>SF: mark F2 delivered (rf_tx) or retry
    Note over ENG,SF: F4 not deliverable -> stays QUEUED,
    Note over ENG,SF: retried/expired per policy
```

## 5. Link-state machine

```mermaid
stateDiagram-v2
    [*] --> SEARCHING
    DISABLED --> [*]
    SEARCHING --> ACTIVE: carrier/frame heard
    ACTIVE --> AVAILABLE: signal gone < avail_timeout
    AVAILABLE --> ACTIVE: carrier heard again
    AVAILABLE --> DEGRADED: silent < degraded_timeout
    DEGRADED --> ACTIVE: heard again
    DEGRADED --> UNAVAILABLE: silent > degraded_timeout
    UNAVAILABLE --> RECOVERING: heard again
    RECOVERING --> ACTIVE: heard again (stable)
    SEARCHING --> UNAVAILABLE: no signal (optional)
    any --> DISABLED: channel disabled
```

`ACTIVE/AVAILABLE/RECOVERING` count as "heard" for relay-mode delivery.

## 6. Message lifecycle

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> QUEUED: routed to destinations
    QUEUED --> TRANSMITTING: claimed by TX scheduler
    TRANSMITTING --> DELIVERED: replay ok (rf_tx)
    TRANSMITTING --> RETRYING: replay failed
    RETRYING --> TRANSMITTING: next attempt due
    RETRYING --> FAILED: retry limit
    any --> EXPIRED: TTL exceeded
    any --> DROPPED: rejected/queue full/duplicate
    DELIVERED --> [*]
    FAILED --> [*]
    EXPIRED --> [*]
```

## 7. Altitude optimization loop

```mermaid
flowchart LR
    LM["link quality (RSSI/SNR/coverage/success)"] --> SCORE["score"]
    PX["altitude + health"] --> GATE{"feasible & fresh?"}
    GATE -- no --> REC["RECOVERY / hold"]
    GATE -- yes --> SCORE
    SCORE --> DEC{"persistently poor & headroom?"}
    DEC -- yes --> HOLD["HOLD + cooldown"]
    HOLD --> SUG["advisory climb suggestion"]
    SUG -. operator/guided .-> VEHICLE
    DEC -- no --> MON["MONITORING"]
    MON --> GATE
```

## 8. Dashboard architecture

```mermaid
flowchart TB
    DASH["Single-page dashboard (JS)"] -->|poll 1s| API
    DASH <--|SSE| API
    API --> CFG
    API --> ENG
    API --> LM
    API --> SF
    API --> PX
    API --> ALT
    API --> BUS
    AUTH --> API
```

Reads poll `/api/overview` every second; live events stream over Server-Sent
Events (`/api/stream`). Frequency changes go through `PUT /api/config/channels`
which validates, persists, then pushes the new frequency set to the radio
subsystem.
