"""HackRF One transmit backend.

The HackRF One is used only for transmission (it is half duplex; its RX
quality is not relied upon). Imported lazily at open().

Note on power: the HackRF One is a low-power SDR. Its usable output is
typically on the order of -10 to +0 dBm across UHF before the onboard
amplifier, and FCC/regulatory duty limits apply. Reaching the handheld FM
radios at any useful distance requires an external power amplifier and
proper RF filtering on the TX chain. See /docs/rf_architecture.md and the BOM.
"""

from __future__ import annotations

from ..util.logger import get_logger
from .sdr import DeviceInfo, RadioDevice, RadioError

log = get_logger("radio.hackrf")


class HackRfDevice(RadioDevice):
    def __init__(self, sample_rate_hz: int = 2_000_000, txvga_db: int = 0) -> None:
        super().__init__()
        self.sample_rate_hz = int(sample_rate_hz)
        self.gain_db = int(txvga_db)
        self.amp_enable = False
        self._hackrf = None
        self._lib = None

    def open(self) -> None:
        try:
            import hackrf
        except Exception as e:  # noqa: BLE001
            raise RadioError(
                "HackRF library unavailable (is libhackrf installed? "
                "run install_raspberrypi.sh)") from e
        try:
            h = hackrf.HackRF()
            h.sample_rate = self.sample_rate_hz
            h.center_freq = 100_000_000
            h.txvga_gain = self.gain_db
            h.enable_amp = self.amp_enable
            self._hackrf = h
            self._lib = hackrf
        except Exception as e:  # noqa: BLE001
            raise RadioError(f"HackRF open failed: {e}") from e
        log.info("HackRF opened", sample_rate=self.sample_rate_hz)

    def close(self) -> None:
        if self._hackrf is not None:
            try:
                self._hackrf.stop_tx()
                self._hackrf.close()
            except Exception:  # noqa: BLE001
                pass
            self._hackrf = None

    def set_sample_rate(self, hz: int) -> None:
        self.sample_rate_hz = int(hz)
        if self._hackrf is not None:
            self._hackrf.sample_rate = self.sample_rate_hz

    def set_rx_gain(self, db: int) -> None:
        # txvga amplifier gain control
        self.gain_db = int(db)
        if self._hackrf is not None:
            self._hackrf.txvga_gain = self.gain_db

    def has_rssi(self) -> bool:
        return False

    def info(self) -> DeviceInfo:
        present = False
        try:
            import hackrf  # noqa: F401
            present = True
        except Exception:  # noqa: BLE001
            present = False
        return DeviceInfo(name="HackRF One (TX path)", backend="hackrf",
                          supports_rx=False, supports_tx=True,
                          min_freq_hz=1_000_000, max_freq_hz=6_000_000_000,
                          present=present and (self._hackrf is not None))

    def _call(self, name: str, *args):
        if hasattr(self._hackrf, name):
            return getattr(self._hackrf, name)(*args)
        raise AttributeError(f"HackRF API has no {name}")

    def tx_iq(self, frequency_hz: int, iq: bytes, power_amp: bool) -> bool:
        """Transmit a single bounded complex-baseband burst, blocking until the
        burst has been clocked out. Different python-hackrf bindings expose
        slightly different methods; we tolerate the two common ones."""
        import time
        if self._hackrf is None:
            raise RadioError("HackRF not open")
        try:
            # Both common python-hackrf bindings expose these as properties.
            try:
                self._hackrf.center_freq = int(frequency_hz)
                self._hackrf.enable_amp = bool(power_amp)
            except Exception:  # noqa: BLE001
                pass
            burst_s = (len(iq) // 2) / float(self.sample_rate_hz)
            try:
                self._call("stop_tx")
            except Exception:  # noqa: BLE001
                pass
            # Streaming-style binding: set fnsamplerate + write buffer.
            try:
                self._call("write", iq)
                self._call("start_tx")
            except Exception:  # noqa: BLE001
                # Buffer/one-shot style binding used by some forks.
                self._call("write", iq)
            time.sleep(burst_s + 0.05)
            try:
                self._call("stop_tx")
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as e:  # noqa: BLE001
            log.error("HackRF TX failed", freq_hz=frequency_hz, error=str(e))
            return False
