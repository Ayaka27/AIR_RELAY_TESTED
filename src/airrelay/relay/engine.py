"""Opaque multi-channel store-and-forward relay engine.

A transmission captured on one channel (as an opaque I/Q clip) is routed to
every other enabled channel. The relay NEVER decodes/decrypts the clip content;
it only moves and replays the captured RF.

Routing:
  * capture on channel S -> for each enabled channel D != S, persist a durable
    message holding the opaque clip and route metadata (QUEUED).
  * When D is open for delivery, replay the clip on D's frequency.
  * If D is not open (no TX device / tx_allowed off / channel disabled, or a
    far relay node not present in control mode), the delivery stays QUEUED and
    is retried when D opens.

Delivery confirmation: user traffic is opaque and simplex radios cannot ACK, so
a successful RF replay is the terminal "delivered" event. (Our own optional
control channel provides node-presence only, never payload ACKs.)

Loop / self-repeat control: because no origin token can be read out of an
opaque signal, loop control relies on (a) strict RX/TX time separation,
(b) a per-channel receive blackout right after a replay so we do not re-capture
our own just-sent clip, and (c) physical antenna/isolation design. This is the
same limitation as real transparent cross-band repeaters and is documented, not
silently claimed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from ..core.config import ConfigManager
from ..core.event_bus import EventBus
from ..core.models import Message, MessageState, RelayEvent, RxClip, utcnow_ms
from ..store.queue_db import MessageStore
from ..util.logger import get_logger
from .link_monitor import LinkMonitor

log = get_logger("relay")


class RelayEngine:
    def __init__(self, config: ConfigManager, store: MessageStore,
                 links: LinkMonitor, radio, bus: EventBus) -> None:
        self.cfg = config
        self.store = store
        self.links = links
        self.radio = radio
        self.bus = bus
        self.node_id = str(config.get("system.name", "relay"))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._recent_clips: dict[str, int] = {}  # (channel,start) -> ts
        self.radio.on_clip(self._on_clip)
        self.radio.on_tx_done(self._on_tx_done)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="relay-engine",
                                        daemon=True)
        self._thread.start()
        log.info("relay engine started (opaque clip relay)", node=self.node_id)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    # ------------------------------------------------------------ ingest
    def _on_clip(self, clip: RxClip, rx_channel: str) -> None:
        """Called by the radio manager for every captured opaque burst."""
        # Loop guard: if we re-capture essentially the same burst within a short
        # window (e.g. our own just-sent replay leaking back), ignore it. Keyed
        # on channel + start time because no token can be read from the signal.
        fp = clip.fingerprint
        now = utcnow_ms()
        if self._recent_clips.get(fp, 0) > now - 5000:
            return
        self._recent_clips[fp] = now
        for k in list(self._recent_clips):
            if now - self._recent_clips[k] > 60000:
                self._recent_clips.pop(k, None)

        self._emit(RelayEvent(kind="receive", source=rx_channel,
                              detail=f"burst {clip.duration_s:.2f}s "
                                     f"peak={clip.peak_rssi_dbm:.0f}dBm"))
        self._route(clip, rx_channel)

    def _route(self, clip: RxClip, src: str) -> None:
        if self.cfg.channel(src) is None:
            return
        # drop oversized clips to bound storage
        max_bytes = int(self.cfg.get("store_forward.max_payload_bytes", 9_000_000))
        if len(clip.iq) > max_bytes:
            self._emit(RelayEvent(kind="drop", source=src,
                                  detail=f"clip {len(clip.iq)}B too large"))
            return
        now = utcnow_ms()
        ttl = int(self.cfg.get("store_forward.ttl_s", 900)) * 1000
        routed = 0
        for ch in self.cfg.channels():
            if not ch.enabled or ch.id == src:
                continue
            m = Message(
                source=src, destination=ch.id, payload=clip.iq,
                payload_type="x-airrelay/iq",
                clip_center_hz=clip.center_hz, clip_rate_hz=clip.sample_rate_hz,
                clip_duration_s=clip.duration_s, clip_peak_dbm=clip.peak_rssi_dbm,
                status=MessageState.QUEUED, dup_fp=clip.fingerprint,
                ttl_ms=ttl, received_at_ms=now)
            self.store.insert(m)
            self._emit(RelayEvent(kind="enqueue", message_id=m.id,
                                  source=src, destination=ch.id,
                                  detail=f"{clip.duration_s:.2f}s clip"))
            self.bus.publish("relay.message", m.to_dict())
            routed += 1
        self.bus.publish("relay.clip", {"source": src, "dest_count": routed,
                                        "duration_s": clip.duration_s})

    # ------------------------------------------------------------- TX result
    def _on_tx_done(self, ref: Any, ok: bool) -> None:
        if not isinstance(ref, dict):
            return
        mid = ref.get("msg_id")
        dst = ref.get("dst")
        if not mid:
            return
        if not ok:
            self._retry_failed_tx(mid, dst)
            return
        # opaque clip successfully replayed = delivered (no ACK possible)
        self.store.ack_delivered(mid, utcnow_ms(), ack="rf_tx")
        self._emit(RelayEvent(kind="delivered", message_id=mid,
                              source=self._src_of(mid), destination=dst,
                              detail="clip replayed"))
        self.bus.publish("relay.delivered", {"message_id": mid})

    def _retry_failed_tx(self, mid: str, dst: Optional[str]) -> None:
        now = utcnow_ms()
        state = self.store.fail_and_retry(
            mid, now,
            float(self.cfg.get("store_forward.retry_interval_s", 5)),
            float(self.cfg.get("store_forward.retry_backoff_factor", 2.0)),
            float(self.cfg.get("store_forward.max_retry_interval_s", 300)),
            int(self.cfg.get("store_forward.max_retry_count", 6)),
            int(self.cfg.get("store_forward.ttl_s", 900)) * 1000,
            dst or "")
        if state in ("FAILED", "EXPIRED", "DROPPED"):
            self._emit(RelayEvent(kind=state.lower(), message_id=mid,
                                  destination=dst, detail=state))
            self.bus.publish("relay.failed", {"message_id": mid, "state": state})
        else:
            self._emit(RelayEvent(kind="retry", message_id=mid, destination=dst))
            self.bus.publish("relay.retry", {"message_id": mid})

    def _src_of(self, mid: str) -> Optional[str]:
        m = self.store.get(mid)
        return m.source if m else None

    # -------------------------------------------------------- deliverability
    def _deliverable(self, dst: str) -> bool:
        if not bool(self.cfg.get("radio.tx_enable", True)):
            return False
        if not self.radio.tx_dev_present():
            return False
        ch = self.cfg.channel(dst)
        if ch is None or not ch.enabled or not ch.tx_allowed:
            return False
        if self.cfg.get("relay.mode", "bridge") == "relay":
            # only attempt relays to a far relay node we can hear present
            return self.links.heard_ok(dst)
        return True

    # ----------------------------------------------------------------- loop
    def _run(self) -> None:
        while self._running:
            try:
                now = utcnow_ms()
                self.store.expire_due(now)
                self._flush_deliverable()
                self.links.evaluate()
                retention = int(self.cfg.get("store_forward.delivered_retention_s", 600))
                self.store.purge_terminal(now - retention * 1000)
                self._reconcile_links()
            except Exception:  # noqa: BLE001
                log.exception("relay loop error")
            time.sleep(float(self.cfg.get("relay.poll_ms", 1.0)) / 1000.0)

    def _flush_deliverable(self) -> None:
        now = utcnow_ms()
        for dst in [c.id for c in self.cfg.channels() if c.enabled]:
            if not self._deliverable(dst):
                continue
            for _ in range(max(1, int(self.cfg.get("relay.max_concurrent_tx", 1)))):
                msg = self.store.claim_next(dst, now)
                if msg is None:
                    break
                self._transmit(msg)

    def _transmit(self, msg: Message) -> None:
        ch = self.cfg.channel(msg.destination)
        if ch is None:
            return
        rate = msg.clip_rate_hz or int(self.cfg.get("radio.rf_sample_rate_hz", 2_000_000))
        self._emit(RelayEvent(kind="transmitting", message_id=msg.id,
                              source=msg.source, destination=msg.destination))
        self.radio.enqueue_tx(msg.destination, msg.payload, ch.frequency_hz,
                              {"msg_id": msg.id, "dst": msg.destination},
                              rate_hz=rate)

    def _emit(self, ev: RelayEvent) -> None:
        self.bus.publish("relay.events", ev.to_dict())

    # ------------------------------------------------------- reconciliation
    def _reconcile_links(self) -> None:
        for st in self.links.all():
            st.queue_len = self.store.destination_queue_len(st.channel_id)
            st.deliveries_ok = self.store.counter(st.channel_id, "deliveries_ok")
            st.deliveries_fail = self.store.counter(st.channel_id, "deliveries_fail")
            self.links.update_full(st)

    def snapshot(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "running": self._running,
            "mode": self.cfg.get("relay.mode", "bridge"),
            "opaque": True,
            "deliverable_when": "rf_tx success (no payload ACK for opaque traffic)",
        }
