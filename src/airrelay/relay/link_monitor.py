"""Continuous per-channel link monitoring.

Tracks RF observations for every configured channel and derives a link
state used both for the dashboard (monitoring) and for relay delivery
decisions. Link state is a monitoring state: it reflects whether the
channel's far end has been *heard* recently (carrier/frames on RX).

Delivery availability is a separate concept and is derived in the relay
engine using this state together with configuration (see relay/engine.py).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..core.models import ChannelStatus, LinkState, utcnow_ms
from ..util.logger import get_logger

log = get_logger("link")

# states that indicate we are currently hearing the far end
RECEIVING = {LinkState.ACTIVE}
HEARD = {LinkState.ACTIVE, LinkState.AVAILABLE, LinkState.RECOVERING}


class LinkMonitor:
    def __init__(self, config) -> None:
        self.cfg = config
        self._lock = threading.RLock()
        self._channels: dict[str, ChannelStatus] = {}
        self._alpha = float(config.get("link_monitor.rssi_smooth_alpha", 0.3))
        self._avail_ms = int(config.get("link_monitor.available_timeout_ms", 5000))
        self._deg_ms = int(config.get("link_monitor.degraded_timeout_ms", 20000))
        self._threshold = float(config.get("radio.rx_min_carrier_dbm", -110.0))
        self._init_channels()

    def _init_channels(self) -> None:
        for ch in self.cfg.channels():
            self._channels[ch.id] = ChannelStatus(
                channel_id=ch.id, frequency_hz=ch.frequency_hz,
                state=LinkState.DISABLED if not ch.enabled else LinkState.SEARCHING)

    def reconfigure(self) -> None:
        """After a channel/frequency change, rebuild channel status."""
        with self._lock:
            old = self._channels
            self._channels = {}
            for ch in self.cfg.channels():
                prev = old.get(ch.id)
                if prev:
                    prev.frequency_hz = ch.frequency_hz
                    if not ch.enabled:
                        prev.state = LinkState.DISABLED
                    self._channels[ch.id] = prev
                else:
                    self._channels[ch.id] = ChannelStatus(
                        channel_id=ch.id, frequency_hz=ch.frequency_hz,
                        state=LinkState.SEARCHING)

    # -------------------------------------------------------------- updates
    def observe_rx(self, channel: str, peak_dbm: Optional[float],
                   clip: object = None) -> None:
        """Report an observation on `channel`. When a clip was captured the
        channel was active; otherwise `peak_dbm` is a below-squelch scan level
        (quiet). User signal is opaque: only energy level is recorded."""
        now = utcnow_ms()
        st = self._channels.get(channel)
        if st is None:
            return
        with self._lock:
            if peak_dbm is not None and peak_dbm > float("-inf"):
                if st.last_rssi_dbm is None:
                    st.last_rssi_dbm = peak_dbm
                else:
                    a = self._alpha
                    st.last_rssi_dbm = a * peak_dbm + (1 - a) * st.last_rssi_dbm
            if clip is not None:
                st.last_rx_at_ms = now
                st.activity_count += 1
                st.signal_active = True
            elif peak_dbm is not None and peak_dbm >= self._threshold:
                st.last_rx_at_ms = now
                st.activity_count += 1
                st.signal_active = True
            else:
                st.signal_active = False
            st.updated_at_ms = now

    def observe_tx(self, channel: str, ok: bool) -> None:
        now = utcnow_ms()
        st = self._channels.get(channel)
        if st is None:
            return
        with self._lock:
            st.last_tx_at_ms = now
            if ok:
                st.last_tx_ok_at_ms = now
                st.deliveries_ok += 0  # real delivery counters live in the store
            else:
                st.deliveries_fail += 0

    # ------------------------------------------------------------ evaluation
    def evaluate(self) -> None:
        """Transition link states based on time since last heard signal."""
        now = utcnow_ms()
        with self._lock:
            for st in self._channels.values():
                if st.state == LinkState.DISABLED:
                    continue
                ch = self.cfg.channel(st.channel_id)
                if ch is not None and not ch.enabled:
                    st.state = LinkState.DISABLED
                    continue
                last = st.last_rx_at_ms
                if last is None:
                    st.state = LinkState.SEARCHING
                    st.quality = 0.0
                    continue
                age = now - last
                if st.signal_active:
                    st.state = LinkState.ACTIVE
                    st.quality = min(1.0, st.quality + 0.1)
                elif age <= self._avail_ms:
                    st.state = LinkState.AVAILABLE
                elif age <= self._deg_ms:
                    st.state = LinkState.DEGRADED
                    st.quality = max(0.0, st.quality - 0.05)
                else:
                    st.state = LinkState.UNAVAILABLE
                    st.quality = 0.0
                if st.state == LinkState.ACTIVE and st.last_rx_at_ms and \
                        age > self._avail_ms:
                    pass

    def update_full(self, status: ChannelStatus) -> None:
        """Write externally-computed fields (queue len, delivery counters)
        back into the stored status for a channel."""
        st = self._channels.get(status.channel_id)
        if st is None:
            return
        with self._lock:
            st.queue_len = status.queue_len
            st.deliveries_ok = status.deliveries_ok
            st.deliveries_fail = status.deliveries_fail

    def get(self, channel: str) -> ChannelStatus | None:
        with self._lock:
            st = self._channels.get(channel)
            if st is None:
                return None
            return ChannelStatus(**st.__dict__)

    def all(self) -> list[ChannelStatus]:
        return [self.get(c.id) for c in self.cfg.channels()
                if self.get(c.id) is not None]

    def snapshot(self) -> dict[str, dict]:
        return {st.channel_id: st.to_dict() for st in self.all()}

    def heard_ok(self, channel: str) -> bool:
        st = self.get(channel)
        return st is not None and st.state in HEARD
