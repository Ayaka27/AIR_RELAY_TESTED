"""Central, persistent, validated configuration.

Single source of truth consumed by the dashboard, backend, relay engine,
radio manager and flight layers. Frequencies are never hard-coded: the
operator sets them through the dashboard and they are stored here and
pushed to the radio subsystem.

Stored as a JSON document. Changes are validated, then applied atomically
(atomic write) and broadcast so live subsystems can react cleanly.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Callable

from .models import ChannelConfig
from ..util.logger import get_logger

log = get_logger("config")


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Defaults and validation schema (dotted paths)
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "system": {
        "name": "air-relay-1",
        "log_level": "INFO",
        "timezone": "UTC",
    },
    "band_limits": {
        # Intersection of RTL-SDR receive range and allowed operating band.
        "min_mhz": 24.0,
        "max_mhz": 1700.0,
        # Handhelds used in tests typically operate inside this guard band.
        "guard_band_khz": 25.0,
    },
    "channels": [
        {"id": "F1", "label": "Channel 1", "enabled": True, "frequency_mhz": 145.500,
         "tx_allowed": True, "rx_allowed": True},
        {"id": "F2", "label": "Channel 2", "enabled": True, "frequency_mhz": 146.700,
         "tx_allowed": True, "rx_allowed": True},
        {"id": "F3", "label": "Channel 3", "enabled": True, "frequency_mhz": 433.250,
         "tx_allowed": True, "rx_allowed": True},
        {"id": "F4", "label": "Channel 4", "enabled": True, "frequency_mhz": 446.125,
         "tx_allowed": True, "rx_allowed": True},
    ],
    "radio": {
        # Transparent opaque relay: the relay detects RF energy and replays the
        # captured I/Q unchanged. It NEVER decodes/decrypts user traffic.
        "architecture": "opaque_iq_time_division",
        "rx_device": "rtl",        # auto | rtl | none
        "tx_device": "hackrf",     # auto | hackrf | none
        "rf_sample_rate_hz": 2_000_000,   # RF IQ rate (HackRF needs >=2 MS/s)
        "rx_gain_db": 0,           # 0 = auto
        "tx_enable": True,
        "tx_amp_enable": False,
        "txvga_gain_db": 0,
        # time-division scanning / capture budget
        "scan_order": ["F1", "F2", "F3", "F4"],
        "capture": {
            "listen_s": 0.35,      # dwell per channel while scanning for a burst
            "max_clip_s": 1.0,     # longest clip kept if a burst continues
            "tail_s": 0.25,        # silence that ends a captured burst
            "post_tx_guard_s": 0.6,  # do not listen on a channel right after TX
        },
        # RF energy (squelch) detection - the only thing done to user signal
        "squelch": {
            "enable": True,
            "open_dbm": -95.0,
            "hold_dbm": -101.0,    # close once energy falls below this
        },
        "rx_min_carrier_dbm": -110.0,
    },
    "control": {
        # Relay-node control plane, kept strictly separate from user channels.
        # Off by default. When on, the relay exchanges its OWN low-rate AFSK
        # beacons on a designated frequency to learn far-node presence. User
        # traffic is never used for control and never decoded.
        "enabled": False,
        "frequency_mhz": 434.0,
        "baud": 1200,
        "heartbeat_s": 10,
        "timeout_s": 30,
    },
    "link_monitor": {
        "poll_ms": 1000,
        "available_timeout_ms": 5000,   # no frame/probe => DEGRADED
        "degraded_timeout_ms": 20000,   # => UNAVAILABLE
        "recovery_probes": 1,
        "history_len": 50,
        "rssi_smooth_alpha": 0.3,
    },
    "relay": {
        "mode": "bridge",            # bridge | relay
        "confirm": "rf_tx",          # rf_tx | ack
        "poll_ms": 500,
        "dedupe_window_ms": 60000,
        "max_concurrent_tx": 1,
    },
    "store_forward": {
        "max_queue_size": 500,
        "max_retry_count": 6,
        "retry_interval_s": 5,
        "retry_backoff_factor": 2.0,
        "max_retry_interval_s": 300,
        "ttl_s": 900,
        "tx_window_ms": 800,          # per-message airtime budget
        "max_payload_bytes": 9_000_000,  # opaque I/Q clip cap (~1.1 s @2MS/s per destination)
        "max_clip_s": 1.0,            # match radio.capture.max_clip_s
        "delivered_retention_s": 600,
    },
    "flight": {
        "enabled": False,
        "connection": "udpin:127.0.0.1:14550",
        "source_system": 255,
        "baud": 115200,
        "poll_ms": 1000,
    },
    "altitude": {
        "enabled": False,
        "min_alt_m": 50,
        "max_alt_m": 400,
        "hysteresis_m": 15,
        "stabilization_s": 20,
        "cooldown_s": 30,
        "score": {
            "w_rssi": 1.0,
            "w_snr": 1.0,
            "w_coverage": 2.0,
            "w_stability": 1.0,
            "w_altitude_cost": 0.5,
            "rssi_target_dbm": -85.0,
            "snr_target_db": 12.0,
        },
        "alt_step_m": 20,
    },
    "dashboard": {
        "host": "0.0.0.0",
        "port": 8080,
        "auth_enabled": False,
        "session_ttl_s": 7200,
        "public_link_read": True,
    },
    "logging": {"file_enabled": True, "level": "INFO"},
    "dev": {"allow_inject": False},
}

SCHEMA: dict[str, dict[str, Any]] = {
    "system.log_level": {"type": "str", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    "radio.rx_device": {"type": "str", "choices": ["auto", "rtl", "none"]},
    "radio.tx_device": {"type": "str", "choices": ["auto", "hackrf", "none"]},
    "radio.rf_sample_rate_hz": {"type": "int", "min": 250_000, "max": 6_000_000},
    "radio.capture.listen_s": {"type": "float", "min": 0.05, "max": 5.0},
    "radio.capture.max_clip_s": {"type": "float", "min": 0.1, "max": 60.0},
    "link_monitor.available_timeout_ms": {"type": "int", "min": 1000, "max": 120000},
    "store_forward.max_queue_size": {"type": "int", "min": 1, "max": 100000},
    "store_forward.max_retry_count": {"type": "int", "min": 0, "max": 100},
    "store_forward.retry_interval_s": {"type": "float", "min": 0.5, "max": 3600},
    "store_forward.ttl_s": {"type": "float", "min": 5, "max": 86400},
    "altitude.min_alt_m": {"type": "float", "min": 0, "max": 10000},
    "altitude.max_alt_m": {"type": "float", "min": 0, "max": 10000},
    "dashboard.host": {"type": "str"},
    "dashboard.port": {"type": "int", "min": 1, "max": 65535},
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize_freq(mhz: float) -> int:
    """Convert an operator MHz entry (MHz floating) to integer Hz without float loss."""
    return int(round(float(mhz) * 1_000_000))


def validate_freq_hz(hz: int, limits: dict[str, float]) -> None:
    lo = int(limits["min_mhz"] * 1e6)
    hi = int(limits["max_mhz"] * 1e6)
    if not (lo <= hz <= hi):
        raise ConfigError(
            f"frequency {hz} Hz out of supported band "
            f"[{limits['min_mhz']}-{limits['max_mhz']} MHz]"
        )


class ConfigManager:
    def __init__(self, config_file: Path) -> None:
        self._file = Path(config_file)
        self._lock = threading.RLock()
        self._config: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.revision = 0
        self._apply_hooks: list[Callable[[int], None]] = []
        self._load()

    # -- load / persist -----------------------------------------------------
    def _load(self) -> None:
        if self._file.exists():
            try:
                with open(self._file, "r", encoding="utf-8") as fh:
                    overlay = json.load(fh)
                self._config = _deep_merge(self._config, overlay)
            except (json.JSONDecodeError, OSError) as e:
                log.error("config load failed, using defaults", error=str(e))
        else:
            self.persist()

    def persist(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._config, fh, indent=2, sort_keys=False)
        tmp.replace(self._file)  # atomic on POSIX

    # -- access ---------------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._config
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return copy.deepcopy(node)

    def set(self, dotted: str, value: Any) -> None:
        validators = _schema_validators(dotted)
        for fn in validators:
            fn(dotted, value)
        with self._lock:
            node = self._config
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = copy.deepcopy(value)
            self.revision += 1
            self.persist()
        for hook in list(self._apply_hooks):
            try:
                hook(self.revision)
            except Exception:  # noqa: BLE001
                log.exception("apply hook failed")

    def on_change(self, hook: Callable[[int], None]) -> None:
        self._apply_hooks.append(hook)

    def limits(self) -> dict[str, float]:
        return dict(self.get("band_limits"))

    # -- channel model ---------------------------------------------------------
    def channels_raw(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.get("channels", []))

    def channels(self) -> list[ChannelConfig]:
        out: list[ChannelConfig] = []
        for c in self.channels_raw():
            cfg = ChannelConfig(
                id=str(c["id"]),
                label=str(c.get("label", c["id"])),
                enabled=bool(c.get("enabled", True)),
                frequency_hz=normalize_freq(c.get("frequency_mhz", 0)),
                tx_allowed=bool(c.get("tx_allowed", True)),
                rx_allowed=bool(c.get("rx_allowed", True)),
            )
            out.append(cfg)
        return out

    def channel(self, channel_id: str) -> ChannelConfig | None:
        for c in self.channels():
            if c.id == channel_id:
                return c
        return None

    def _validate_channel_set(self, chans: list[dict[str, Any]]) -> None:
        if not chans:
            raise ConfigError("at least one channel is required")
        lim = self.limits()
        seen: set[int] = set()
        ids: set[str] = set()
        for c in chans:
            cid = str(c["id"])
            if cid in ids:
                raise ConfigError(f"duplicate channel id {cid}")
            ids.add(cid)
            hz = normalize_freq(c.get("frequency_mhz", 0))
            if not c.get("enabled"):
                continue
            validate_freq_hz(hz, lim)
            # duplicate frequency guard applies between enabled channels only
            if hz in seen:
                raise ConfigError(
                    f"conflicting frequency {hz/1e6:.3f} MHz assigned to "
                    f"two enabled channels"
                )
            seen.add(hz)

    def apply_channels(self, raw: list[dict[str, Any]], persist: bool = True) -> None:
        """Validate and atomically replace the active channel set."""
        normalized = []
        for c in raw:
            normalized.append({
                "id": str(c["id"]),
                "label": str(c.get("label", c["id"])),
                "enabled": bool(c.get("enabled", True)),
                "frequency_mhz": normalize_freq(c.get("frequency_mhz", 0)) / 1e6,
                "tx_allowed": bool(c.get("tx_allowed", True)),
                "rx_allowed": bool(c.get("rx_allowed", True)),
            })
        self._validate_channel_set(normalized)
        with self._lock:
            self._config["channels"] = normalized
            self.revision += 1
            if persist:
                self.persist()
        log.info("channel configuration applied", channels=[
            {"id": c["id"], "freq_mhz": c["frequency_mhz"], "enabled": c["enabled"]}
            for c in normalized])
        for hook in list(self._apply_hooks):
            try:
                hook(self.revision)
            except Exception:  # noqa: BLE001
                log.exception("apply hook failed")

    # -- dashboard export ---------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "revision": self.revision,
                "config": copy.deepcopy(self._config),
                "band_limits": self.limits(),
            }


def _schema_validators(dotted: str) -> list[Callable[[str, Any], None]]:
    spec = SCHEMA.get(dotted)
    if spec is None:
        return []

    def validate(path: str, value: Any) -> None:
        t = spec["type"]
        try:
            if t == "int":
                value = int(value)
            elif t == "float":
                value = float(value)
            elif t == "bool":
                value = bool(value)
            elif t == "str":
                value = str(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{path}: invalid {t} value {value!r}")  # noqa: BLE001
        if "min" in spec and value < spec["min"]:
            raise ConfigError(f"{path}: below minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ConfigError(f"{path}: above maximum {spec['max']}")
        if "choices" in spec and value not in spec["choices"]:
            raise ConfigError(f"{path}: must be one of {spec['choices']}")

    return [validate]
