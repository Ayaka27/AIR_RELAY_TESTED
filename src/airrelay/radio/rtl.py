"""RTL-SDR receive/capture backend.

Captures opaque I/Q. It tunes once and streams blocks, measuring RF energy per
block to implement squelch-gated burst capture. It never demodulates or parses
the received signal (the relay treats user traffic as opaque).

Imported lazily at open() so the rest of the project can start without the
RTL-SDR tools installed.
"""

from __future__ import annotations

import math

from ..core.models import RxClip
from ..util.logger import get_logger
from .sdr import DeviceInfo, RadioDevice, RadioError

log = get_logger("radio.rtl")

_BLOCK_SAMPLES = 65536  # tune/read chunk


def _block_power_dbm(iq_bytes: bytes) -> float:
    """RMS power (relative dBm) of an interleaved int16 I/Q block."""
    n = len(iq_bytes) // 4
    if n == 0:
        return float("-inf")
    import array
    a = array.array("h")
    a.frombytes(iq_bytes[: len(iq_bytes) - (len(iq_bytes) % 2)])
    # complex power
    acc = 0.0
    vals = a.tolist()
    for i in range(0, len(vals) - 1, 2):
        acc += vals[i] ** 2 + vals[i + 1] ** 2
    rms = math.sqrt(acc / max(1, n)) / 32767.0
    if rms <= 1e-9:
        return float("-inf")
    return 20.0 * math.log10(rms) + 10.0


class RtlSdrDevice(RadioDevice):
    def __init__(self, sample_rate_hz: int = 2_000_000, gain_db: int = 0) -> None:
        super().__init__()
        self.sample_rate_hz = int(sample_rate_hz)
        self.gain_db = int(gain_db)
        self._dev = None

    def open(self) -> None:
        try:
            from rtlsdr import RtlSdr
        except Exception as e:  # noqa: BLE001
            raise RadioError(
                "RTL-SDR library unavailable (run install_raspberrypi.sh)") from e
        try:
            dev = RtlSdr()
            dev.sample_rate = self.sample_rate_hz
            dev.center_freq = 100_000_000
            dev.gain = self.gain_db if self.gain_db else "auto"
            self._dev = dev
        except Exception as e:  # noqa: BLE001
            raise RadioError(f"RTL-SDR open failed: {e}") from e
        log.info("RTL-SDR opened", sample_rate=self.sample_rate_hz)

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                pass
            self._dev = None

    def set_sample_rate(self, hz: int) -> None:
        self.sample_rate_hz = int(hz)
        if self._dev is not None:
            self._dev.sample_rate = self.sample_rate_hz

    def set_rx_gain(self, db: int) -> None:
        self.gain_db = int(db)
        if self._dev is not None:
            self._dev.gain = self.gain_db if self.gain_db else "auto"

    def info(self) -> DeviceInfo:
        present = False
        try:
            import rtlsdr  # noqa: F401
            present = True
        except Exception:  # noqa: BLE001
            present = False
        return DeviceInfo(name="RTL-SDR (capture/RX)", backend="rtl",
                          supports_rx=True, supports_tx=False,
                          min_freq_hz=24_000_000, max_freq_hz=1_700_000_000,
                          present=present and (self._dev is not None))

    # -- squelch-gated burst capture (no demod) -----------------------------
    def capture(self, center_hz: int, detect_s: float, max_clip_s: float,
                squelch_open_dbm: float, tail_s: float,
                end_reason: str = "") -> RxClip | None:
        if self._dev is None:
            raise RadioError("RTL-SDR not open")
        sr = self.sample_rate_hz
        try:
            self._dev.center_freq = int(center_hz)
            # warm-up so residual carrier from a previous tune is flushed
            self._dev.read_samples(int(sr * 0.01))
        except Exception as e:  # noqa: BLE001
            raise RadioError(f"RTL-SDR tune failed @ {center_hz}: {e}") from e

        chunks = bytearray()
        peak = float("-inf")
        import time
        silence = 0.0
        started_at = time.monotonic()
        detect_until = started_at + detect_s
        detecting = True

        while True:
            now = time.monotonic()
            # detection window expired without a burst -> return None
            if detecting and now >= detect_until:
                return None
            try:
                block = self._dev.read_samples(_BLOCK_SAMPLES)
            except Exception as e:  # noqa: BLE001
                log.warning("RTL capture read failed", error=str(e))
                return None
            if not isinstance(block, bytes):
                block = (block.astype("int16").tobytes() if hasattr(block, "astype")
                         else bytes(block))
            dbm = _block_power_dbm(block)
            peak = max(peak, dbm)
            if dbm >= squelch_open_dbm:
                detecting = False
                chunks += block
                silence = 0.0
            elif not detecting:
                chunks += block
                silence += len(block) / 4 / sr
                if silence >= tail_s:
                    break
            if not detecting and (time.monotonic() - started_at) >= max_clip_s:
                break

        if len(chunks) == 0:
            return None
        dur = (len(chunks) / 4) / sr
        return RxClip(source_channel="", start_ms=int(time.time() * 1000),
                      center_hz=int(center_hz), sample_rate_hz=sr,
                      duration_s=dur, peak_rssi_dbm=peak, iq=bytes(chunks))
