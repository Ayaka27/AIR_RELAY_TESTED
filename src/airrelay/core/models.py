"""Core data models shared across the relay, radio and dashboard layers."""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def utcnow_ms() -> int:
    return int(time.time() * 1000)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_message_id() -> str:
    return uuid.uuid4().hex[:16]


class MessageState(str, enum.Enum):
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    TRANSMITTING = "TRANSMITTING"
    DELIVERED = "DELIVERED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    DROPPED = "DROPPED"  # rejected (queue full / invalid / duplicate)


class LinkState(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    SEARCHING = "SEARCHING"
    AVAILABLE = "AVAILABLE"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    RECOVERING = "RECOVERING"
    DISABLED = "DISABLED"


class AltState(str, enum.Enum):
    MANUAL = "MANUAL"
    MONITORING = "MONITORING"
    OPTIMIZATION = "OPTIMIZATION"
    HOLD = "HOLD"
    RECOVERY = "RECOVERY"


class FlightMode(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    MANUAL = "MANUAL"
    GUIDED = "GUIDED"
    LOITER = "LOITER"
    RTL = "RTL"
    AUTO = "AUTO"
    LAND = "LAND"


@dataclass
class ChannelConfig:
    id: str  # logical name, e.g. "F1"
    label: str = ""
    enabled: bool = True
    frequency_hz: int = 145_500_000
    tx_allowed: bool = True
    rx_allowed: bool = True
    # Carrier-sense settings applied by the transmit scheduler.
    cs_dwell_ms: int = 120

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.id

    @property
    def freq_mhz(self) -> float:
        return self.frequency_hz / 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "enabled": self.enabled,
            "frequency_hz": self.frequency_hz,
            "frequency_mhz": self.freq_mhz,
            "tx_allowed": self.tx_allowed,
            "rx_allowed": self.rx_allowed,
        }


@dataclass
class RxClip:
    """An opaque captured transmission. The relay never decodes/decrypts the
    content — this is raw complex-baseband I/Q samples captured while a
    channel was active, plus routing metadata."""

    source_channel: str
    start_ms: int = field(default_factory=utcnow_ms)
    center_hz: int = 0            # RF centre of the channel it was heard on
    sample_rate_hz: int = 2_000_000
    duration_s: float = 0.0
    peak_rssi_dbm: float = float("-inf")
    iq: bytes = b""               # interleaved int16 I/Q, opaque

    @property
    def fingerprint(self) -> str:
        """Loop/duplicate key for a burst. We cannot read a token out of the
        signal, so we key on where+when it started (approximate by design)."""
        return f"{self.source_channel}:{self.start_ms}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_channel": self.source_channel,
            "start_ms": self.start_ms,
            "center_hz": self.center_hz,
            "sample_rate_hz": self.sample_rate_hz,
            "duration_s": round(self.duration_s, 3),
            "peak_rssi_dbm": self.peak_rssi_dbm,
            "iq_bytes": len(self.iq),
        }


@dataclass
class Frame:
    """Control-plane frame (relay-node housekeeping only). Never used on user
    traffic, which is opaque. Kept for the optional control channel."""

    source_channel: str
    payload: bytes
    rx_frequency_hz: int
    rssi_dbm: float
    snr_db: Optional[float] = None
    received_at_ms: int = field(default_factory=utcnow_ms)

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Message:
    """Persistent store-and-forward unit."""

    id: str = field(default_factory=new_message_id)
    origin: Optional[str] = None   # dedupe/origin id broadcast over the air
    seq: int = 0
    dup_fp: Optional[str] = None
    source: str = ""
    destination: str = ""
    payload: bytes = b""           # opaque I/Q clip samples (never decoded)
    payload_type: str = "x-airrelay/iq"
    clip_center_hz: int = 0
    clip_rate_hz: int = 2_000_000
    clip_duration_s: float = 0.0
    clip_peak_dbm: Optional[float] = None
    received_at_ms: int = field(default_factory=utcnow_ms)
    created_at: str = field(default_factory=iso_now)
    priority: int = 0  # 0 normal, higher = more important
    status: MessageState = MessageState.RECEIVED
    retry_count: int = 0
    last_attempt_ms: Optional[int] = None
    next_attempt_ms: Optional[int] = None
    delivered_at_ms: Optional[int] = None
    ttl_ms: Optional[int] = None
    delivery_ack: Optional[str] = None

    def __post_init__(self) -> None:
        if self.origin is None:
            self.origin = self.id

    def age_ms(self, now: int | None = None) -> int:
        now = now or utcnow_ms()
        return max(0, now - self.received_at_ms)

    def queue_age_ms(self, now: int | None = None) -> int:
        now = now or utcnow_ms()
        start = self.received_at_ms
        return max(0, now - start)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "origin": self.origin,
            "source": self.source,
            "destination": self.destination,
            "payload_type": self.payload_type,
            # payload is opaque I/Q; never decode as text
            "payload_bytes": len(self.payload),
            "clip": {
                "center_hz": self.clip_center_hz,
                "rate_hz": self.clip_rate_hz,
                "duration_s": round(self.clip_duration_s, 3),
                "peak_dbm": self.clip_peak_dbm,
            },
            "received_at_ms": self.received_at_ms,
            "created_at": self.created_at,
            "priority": self.priority,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "last_attempt_ms": self.last_attempt_ms,
            "next_attempt_ms": self.next_attempt_ms,
            "delivered_at_ms": self.delivered_at_ms,
            "ttl_ms": self.ttl_ms,
            "delivery_ack": self.delivery_ack,
            "age_ms": self.age_ms(),
        }
        return d


@dataclass
class ChannelStatus:
    channel_id: str
    frequency_hz: int = 0
    state: LinkState = LinkState.UNKNOWN
    last_rssi_dbm: Optional[float] = None
    last_snr_db: Optional[float] = None
    signal_active: bool = False
    last_rx_at_ms: Optional[int] = None
    last_tx_at_ms: Optional[int] = None
    last_tx_ok_at_ms: Optional[int] = None
    queue_len: int = 0
    deliveries_ok: int = 0
    deliveries_fail: int = 0
    frames_seen: int = 0
    activity_count: int = 0
    quality: float = 0.0  # 0..1
    updated_at_ms: int = field(default_factory=utcnow_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RelayEvent:
    ts_ms: int = field(default_factory=utcnow_ms)
    kind: str = ""  # receive|enqueue|transmit_ok|transmit_fail|delivered|retry|expire|drop
    message_id: Optional[str] = None
    source: Optional[str] = None
    destination: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AltitudeSuggestion:
    state: AltState = AltState.MONITORING
    suggested_alt_m: Optional[float] = None
    current_alt_m: Optional[float] = None
    score: float = 0.0
    reason: str = ""
    updated_at: str = field(default_factory=iso_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
