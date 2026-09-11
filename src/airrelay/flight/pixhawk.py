"""Pixhawk flight-controller interface (MAVLink).

Uses pymavlink over a serial or UDP endpoint. It is a pure telemetry
consumer: it NEVER issues flight commands. A dashboard/network/relay failure
therefore cannot drive the vehicle. If telemetry is lost, the interface
enters a disconnected/degraded state and the altitude optimizer is
immediately held — it never commands on stale data.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from ..core.event_bus import EventBus
from ..core.models import FlightMode
from ..util.logger import get_logger

log = get_logger("flight")


class PixhawkInterface:
    def __init__(self, config, bus: EventBus) -> None:
        self.cfg = config
        self.bus = bus
        self.conn = None
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._conn_str = str(config.get("flight.connection", "udpin:127.0.0.1:14550"))
        self._poll_ms = int(config.get("flight.poll_ms", 1000))
        self._connected = False
        self._last_heartbeat = 0.0
        self._data: dict[str, Any] = {
            "connected": False, "alt_amsl_m": None, "alt_rel_m": None,
            "lat": None, "lon": None, "mode": FlightMode.UNKNOWN.value,
            "armed": False, "battery_v": None, "battery_pct": None,
            "vehicle_healthy": False, "gps_fix": False,
        }

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="pixhawk",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self.conn:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None

    def _connect(self) -> None:
        from pymavlink import mavutil
        self.conn = mavutil.mavlink_connection(self._conn_str, source_system=int(
            self.cfg.get("flight.source_system", 255)))
        log.info("Pixhawk connecting", endpoint=self._conn_str)

    # ----------------------------------------------------------------- loop
    def _run(self) -> None:
        while self._running:
            try:
                if self.conn is None:
                    try:
                        self._connect()
                    except Exception as e:  # noqa: BLE001
                        self._set_connected(False)
                        time.sleep(3)
                        continue
                msg = self.conn.recv_match(blocking=True, timeout=1.0)
                if msg is None:
                    # no message received this window -> check heartbeat age
                    if self._connected and time.time() - self._last_heartbeat > 5:
                        self._set_connected(False)
                    continue
                if msg.get_type() == "HEARTBEAT":
                    self._last_heartbeat = time.time()
                    self._set_connected(True)
                    self._parse_heartbeat(msg)
                else:
                    self._parse_message(msg)
            except Exception as e:  # noqa: BLE001
                log.warning("Pixhawk loop error", error=str(e))
                self._set_connected(False)
                time.sleep(2)
        if self.conn:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _parse_heartbeat(self, msg) -> None:
        with self._lock:
            try:
                from pymavlink import mavutil
                mode = mavutil.mode_string_v10(msg)
            except Exception:  # noqa: BLE001
                mode = "UNKNOWN"
            self._data["mode"] = self._map_mode(mode)
            base = getattr(msg, "base_mode", 0)
            self._data["armed"] = bool(base & 0x80) if base is not None else False
            self._data["vehicle_healthy"] = bool(base & 0x40) if base is not None else False

    def _parse_message(self, msg) -> None:
        t = msg.get_type()
        with self._lock:
            if t == "GLOBAL_POSITION_INT":
                self._data["alt_amsl_m"] = msg.alt / 1000.0
                self._data["alt_rel_m"] = msg.relative_alt / 1000.0
                self._data["lat"] = msg.lat / 1e7
                self._data["lon"] = msg.lon / 1e7
            elif t == "BATTERY_STATUS":
                self._data["battery_v"] = msg.voltages[0] / 1000.0 if msg.voltages else None
                if msg.battery_remaining >= 0:
                    self._data["battery_pct"] = msg.battery_remaining
            elif t == "GPS_RAW_INT":
                self._data["gps_fix"] = msg.fix_type >= 3

    def _map_mode(self, mode: str) -> str:
        m = (mode or "").upper()
        for name in ("GUIDED", "LOITER", "RTL", "AUTO", "LAND", "MANUAL"):
            if name in m:
                return name
        return "UNKNOWN"

    def _set_connected(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        with self._lock:
            self._data["connected"] = connected
        if not connected:
            log.warning("Pixhawk link lost — optimizer will hold")
        else:
            log.info("Pixhawk link established")

    # ----------------------------------------------------------------- read
    def telemetry(self) -> dict[str, Any]:
        with self._lock:
            d = dict(self._data)
        d["connected"] = self._connected
        d["endpoint"] = self._conn_str
        self.bus.publish("flight.telemetry", d)
        return d

    @property
    def connected(self) -> bool:
        return self._connected

    def send_ground_cmd(self, cmd: str) -> dict[str, Any]:
        """No flight commands are ever issued from the relay. Only no-op or
        read-only acknowledgements exist here. Kept to make the safety policy
        explicit and auditable."""
        return {"ok": False, "error": "relay never issues flight commands"}
