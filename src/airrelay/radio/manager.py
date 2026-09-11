"""Time-division radio manager (opaque clip relay).

Hardware reality (docs/rf_architecture.md):
  * RTL-SDR receives ONE channel at a time; HackRF is half-duplex TX.
  * RX and TX cannot overlap (HackRF TX would desense the RTL-SDR).
  * User traffic is opaque: we never decode/decrypt it.

Therefore the relay runs a time-division schedule from a single thread:

    per scheduler cycle:
        if a TX (replay) job is pending and the last action was capture:
            replay one queued I/Q clip on its destination channel
            -> blackout that channel for post_tx_guard_s
        else:
            dwell RTL-SDR on the next enabled channel and squelch-capture any
            active burst as opaque I/Q; hand complete clips to the relay

No FM demodulation or AFSK decoding is performed on user traffic. (An optional
control beacon uses the relay's OWN low-rate AFSK on a separate control
frequency; see `control` config, off by default.)
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..core.config import ConfigManager
from ..core.models import ChannelConfig, RxClip, utcnow_ms
from ..util.logger import get_logger
from .hackrf import HackRfDevice
from .rtl import RtlSdrDevice
from .sdr import NullDevice, RadioDevice

log = get_logger("radio.manager")


@dataclass
class TxJob:
    dst_channel: str
    iq: bytes          # opaque clip, replayed unchanged
    freq_hz: int
    ref: Any
    rate_hz: int = 2_000_000
    power_amp: bool = False


class RadioManager:
    def __init__(self, config: ConfigManager, link_monitor: Any) -> None:
        self.config = config
        self.links = link_monitor
        self.rx_dev: RadioDevice | None = None
        self.tx_dev: RadioDevice | None = None
        self._txq: "queue.Queue[TxJob]" = queue.Queue(maxsize=64)
        self._clip_cb: Optional[Callable[[RxClip, str], None]] = None
        self._tx_done_cb: Optional[Callable[[Any, bool], None]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._last_action_rx = True
        self._blackout_until: dict[str, float] = {}   # channel -> monotonic time
        self._control_dev = None

    # ------------------------------------------------------------- callbacks
    def on_clip(self, cb: Callable[[RxClip, str], None]) -> None:
        """cb(clip, rx_channel): an opaque burst was captured on rx_channel."""
        self._clip_cb = cb

    def on_tx_done(self, cb: Callable[[Any, bool], None]) -> None:
        self._tx_done_cb = cb

    # ------------------------------------------------------------- devices
    def _build_devices(self) -> None:
        cfg = self.config
        rate = int(cfg.get("radio.rf_sample_rate_hz", 2_000_000))
        rx_choice = cfg.get("radio.rx_device", "auto")
        tx_choice = cfg.get("radio.tx_device", "auto")
        if rx_choice in ("auto", "rtl"):
            try:
                self.rx_dev = RtlSdrDevice(sample_rate_hz=rate,
                                           gain_db=int(cfg.get("radio.rx_gain_db", 0)))
                self.rx_dev.open()
            except Exception as e:  # noqa: BLE001
                log.warning("RTL-SDR unavailable; capture path null", error=str(e))
                self.rx_dev = NullDevice("rtl", present=False)
        else:
            self.rx_dev = NullDevice("rtl", present=False)
        if tx_choice in ("auto", "hackrf"):
            try:
                self.tx_dev = HackRfDevice(sample_rate_hz=rate,
                                           txvga_db=int(cfg.get("radio.txvga_gain_db", 0)))
                self.tx_dev.open()
            except Exception as e:  # noqa: BLE001
                log.warning("HackRF unavailable; TX path null", error=str(e))
                self.tx_dev = NullDevice("hackrf", present=False)
        else:
            self.tx_dev = NullDevice("hackrf", present=False)

    def start(self) -> None:
        if self._running:
            return
        self._build_devices()
        self._running = True
        self._thread = threading.Thread(target=self._run, name="radio-manager",
                                        daemon=True)
        self._thread.start()
        log.info("radio manager started (opaque clip relay)",
                 rx=self.rx_dev.info().name, tx=self.tx_dev.info().name)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        for dev in (self.rx_dev, self.tx_dev):
            if dev is not None:
                try:
                    dev.close()
                except Exception:  # noqa: BLE001
                    pass

    # -------------------------------------------------------------- state
    def reconfigure(self) -> None:
        rate = int(self.config.get("radio.rf_sample_rate_hz", 2_000_000))
        for dev in (self.rx_dev, self.tx_dev):
            if dev is not None:
                try:
                    dev.set_sample_rate(rate)
                except Exception:  # noqa: BLE001
                    pass
        log.info("radio reconfigured", rate=rate)

    def device_status(self) -> dict[str, Any]:
        out = {}
        for name, dev in (("rx", self.rx_dev), ("tx", self.tx_dev)):
            if dev is not None:
                info = dev.info()
                out[name] = info.to_dict()
                out[name]["active"] = True
            else:
                out[name] = {"present": False, "active": False}
        out["tx_pending"] = self._txq.qsize()
        return out

    def rx_dev_present(self) -> bool:
        return bool(self.rx_dev is not None and self.rx_dev.info().present)

    def tx_dev_present(self) -> bool:
        return bool(self.tx_dev is not None and self.tx_dev.info().present)

    # -------------------------------------------------------------- TX API
    def enqueue_tx(self, dst_channel: str, iq: bytes, freq_hz: int,
                   ref: Any, rate_hz: int = 2_000_000) -> None:
        job = TxJob(dst_channel, iq, freq_hz, ref, rate_hz,
                    power_amp=bool(self.config.get("radio.tx_amp_enable", False)))
        try:
            self._txq.put_nowait(job)
        except queue.Full:
            log.warning("TX queue full, dropping replay", dst=dst_channel)
            if self._tx_done_cb:
                self._tx_done_cb(ref, False)

    # -------------------------------------------------------- scheduler
    def _run(self) -> None:
        cfg = self.config
        cap = cfg.get("radio.capture", {})
        listen = float(cap.get("listen_s", 0.35))
        max_clip = float(cap.get("max_clip_s", 1.0))
        tail = float(cap.get("tail_s", 0.25))
        guard = float(cap.get("post_tx_guard_s", 0.6))
        sq = cfg.get("radio.squelch", {})
        squelch_open = float(sq.get("open_dbm", -95.0))
        amp = bool(cfg.get("radio.tx_amp_enable", False))
        idx = 0
        while self._running:
            try:
                job = self._txq.get_nowait() if self._last_action_rx else None
            except queue.Empty:
                job = None
            if job is not None and self.tx_dev is not None:
                self._last_action_rx = False
                ok = self._do_tx(job)
                self._blackout_until[job.dst_channel] = time.monotonic() + guard
                self._txq.task_done()
                if self._tx_done_cb:
                    self._tx_done_cb(job.ref, ok)
                continue
            self._last_action_rx = True
            if not self.rx_dev_present():
                time.sleep(0.2)
                continue
            chans = [c for c in cfg.channels() if c.enabled and c.rx_allowed]
            if not chans:
                time.sleep(0.2)
                continue
            idx = idx % len(chans)
            ch = chans[idx]
            idx += 1
            # skip channels still in post-TX blackout
            if time.monotonic() < self._blackout_until.get(ch.id, 0.0):
                time.sleep(0.05)
                continue
            self._capture_channel(ch, listen, max_clip, tail, squelch_open)

    def _capture_channel(self, ch: ChannelConfig, listen: float, max_clip: float,
                         tail: float, squelch_open: float) -> None:
        if self.rx_dev is None:
            return
        try:
            clip = self.rx_dev.capture(ch.frequency_hz, listen, max_clip,
                                       squelch_open, tail)
        except Exception as e:  # noqa: BLE001
            log.warning("capture failed", channel=ch.id, error=str(e))
            self.links.observe_rx(ch.id, None)
            return
        if clip is None:
            self.links.observe_rx(ch.id, float(squelch_open) - 20.0)
            return
        clip.source_channel = ch.id
        self.links.observe_rx(ch.id, clip.peak_rssi_dbm, clip=clip)
        log.info("burst captured", channel=ch.id, dur_s=round(clip.duration_s, 2),
                 peak=round(clip.peak_rssi_dbm, 1), bytes=len(clip.iq))
        if self._clip_cb:
            self._clip_cb(clip, ch.id)

    def _do_tx(self, job: TxJob) -> bool:
        if self.tx_dev is None:
            return False
        try:
            ok = self.tx_dev.tx_iq(job.freq_hz, job.iq, job.power_amp)
        except Exception as e:  # noqa: BLE001
            log.warning("replay TX failed", channel=job.dst_channel, error=str(e))
            ok = False
        if ok:
            log.info("replay transmitted", channel=job.dst_channel,
                     bytes=len(job.iq))
        return ok
