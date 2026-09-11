"""SDR abstraction layer.

The relay engine and radio manager depend only on the narrow `RadioDevice`
interface below. The relay NEVER decodes/decrypts user traffic — user signals
are handled purely as opaque RF energy:

  * detect RF energy on a configured channel,
  * capture the active transmission as an opaque complex-baseband I/Q clip,
  * replay that exact clip on another channel's frequency.

No FM demodulation, no AFSK decoding, no payload parsing of user traffic.
Recovering *our own* optional control beacons is kept strictly separate (and is
off by default).

Physical backends: RTL-SDR (capture/RX) and HackRF One (replay/TX). A
`NullDevice` provides the same interface for software bring-up/tests and never
synthesizes inbound RF.

RF architecture summary (docs/rf_architecture.md):
  * RTL-SDR  = one channel at a time => RX is a time-division scan.
  * HackRF   = half-duplex TX; cannot TX while the RTL-SDR must listen cleanly.
  => RX and TX are serialized by the scheduler in radio/manager.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..core.models import RxClip
from ..util.logger import get_logger

log = get_logger("radio.sdr")


@dataclass
class DeviceInfo:
    name: str
    backend: str
    serial: str = ""
    supports_rx: bool = False
    supports_tx: bool = False
    min_freq_hz: int = 0
    max_freq_hz: int = 0
    present: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class RadioError(RuntimeError):
    pass


class RadioDevice(ABC):
    """Low-level RF I/O every backend must implement. Driven from the manager's
    single scheduling thread, so implementations need not be thread-safe.

    Capturing / replaying happens at the RF baseband rate; the relay never
    demodulates the captured samples.
    """

    def __init__(self) -> None:
        self.sample_rate_hz: int = 2_000_000
        self.gain_db: int = 0

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def set_sample_rate(self, hz: int) -> None: ...

    @abstractmethod
    def set_rx_gain(self, db: int) -> None: ...

    @abstractmethod
    def info(self) -> DeviceInfo: ...

    def has_rssi(self) -> bool:
        return False

    # Listen on `center_hz`. If RF energy above `squelch_open_dbm` is heard
    # within `detect_s`, capture the burst until energy drops below threshold
    # for `tail_s`, or until `max_clip_s` elapses. Returns an RxClip (opaque
    # I/Q, never decoded) or None if no burst was detected within `detect_s`.
    # Only RX-capable backends implement this; the default returns None so a
    # TX-only backend (e.g. HackRF) can still be instantiated.
    def capture(self, center_hz: int, detect_s: float, max_clip_s: float,
                squelch_open_dbm: float, tail_s: float,
                end_reason: str = "") -> Optional[RxClip]:
        return None

    # Transmit one opaque complex-baseband burst (interleaved int16 I/Q) at
    # center `frequency_hz`. The samples are replayed unchanged.
    @abstractmethod
    def tx_iq(self, frequency_hz: int, iq: bytes, power_amp: bool) -> bool: ...


class NullDevice(RadioDevice):
    """Standard interface but no RF. present=False so the dashboard reports no
    RF device. Used only for software bring-up/tests."""

    def __init__(self, backend: str = "null", present: bool = False) -> None:
        super().__init__()
        self._backend = backend
        self._present = present

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def set_sample_rate(self, hz: int) -> None:
        self.sample_rate_hz = int(hz)

    def set_rx_gain(self, db: int) -> None:
        self.gain_db = int(db)

    def info(self) -> DeviceInfo:
        return DeviceInfo(name=f"{self._backend}-null", backend=self._backend,
                          present=self._present)

    def capture(self, center_hz: int, detect_s: float, max_clip_s: float,
                squelch_open_dbm: float, tail_s: float,
                end_reason: str = "") -> Optional[RxClip]:
        # no inbound RF can ever be synthesized here
        return None

    def tx_iq(self, frequency_hz: int, iq: bytes, power_amp: bool) -> bool:
        log.debug("NullDevice.tx_iq requested but no RF device", freq_hz=frequency_hz)
        return False
