"""Application context: builds and wires every subsystem.

One process hosts the radio manager, relay engine, persistent store,
Pixhawk interface, altitude optimizer and the web server. Component health is
still reported individually, but a single process keeps wiring and service
dependency handling simple and avoids cross-process locking surprises.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..core.config import ConfigManager
from ..core.event_bus import EventBus
from ..core.models import Frame, utcnow_ms
from ..flight.altitude import AltitudeOptimizer
from ..flight.pixhawk import PixhawkInterface
from ..radio.manager import RadioManager
from ..relay.engine import RelayEngine
from ..relay.link_monitor import LinkMonitor
from ..store.queue_db import MessageStore
from ..util.logger import get_logger
from ..util.paths import Paths

log = get_logger("context")


class AppContext:
    def __init__(self, home: str | None = None) -> None:
        from ..util.paths import resolve_paths
        self.paths = resolve_paths(home)
        self.paths.ensure()
        self.bus = EventBus()
        self.config = ConfigManager(self.paths.config_file)
        self._log_init()
        self._configure_logging()
        self.store = MessageStore(self.paths.db_path)
        self.links = LinkMonitor(self.config)
        self.radio = RadioManager(self.config, self.links)
        self.relay = RelayEngine(self.config, self.store, self.links,
                                 self.radio, self.bus)
        self.pixhawk = PixhawkInterface(self.config, self.bus)
        self.altitude = AltitudeOptimizer(self.config, self.links,
                                          self.pixhawk, self.bus)
        self._stopped = False
        # subsystem on/off switches (reporting)
        self.component_state = {
            "relay": False, "store": False, "dashboard": False,
            "radio": False, "pixhawk": False, "altitude": False,
        }

    # --------------------------------------------------------------- logging
    def _log_init(self) -> None:
        from ..util import logger
        logger._logger.configure("INFO")

    def _configure_logging(self) -> None:
        # reconfigure with configurable level + file logging
        from ..util import logger
        level = str(self.config.get("logging.level", "INFO"))
        use_file = bool(self.config.get("logging.file_enabled", True))
        logger._logger.configure(level, self.paths.log_dir if use_file else None)

    # ----------------------------------------------------------------- start
    def start(self) -> None:
        self.component_state["store"] = True
        self.component_state["relay"] = True
        self.component_state["dashboard"] = True
        # Relay + radio first so inbound frames are routed.
        self.relay.start()
        self.component_state["relay"] = self.relay.snapshot()["running"]
        self.radio.start()
        self.component_state["radio"] = True
        if bool(self.config.get("flight.enabled", False)):
            self.pixhawk.start()
            self.component_state["pixhawk"] = True
        self.component_state["altitude"] = bool(self.config.get("altitude.enabled", False))
        # background telemetry/altitude sampler
        self._bg_stop = threading.Event()
        self._bg = threading.Thread(target=self._background, daemon=True)
        self._bg.start()
        log.info("context started")

    def _background(self) -> None:
        poll = max(1.0, float(self.config.get("relay.poll_ms", 1.0)) / 1000.0)
        while not self._bg_stop.is_set():
            try:
                if bool(self.config.get("altitude.enabled", False)):
                    self.altitude.step()
                self.bus.publish("system.heartbeat",
                                 {"ts": utcnow_ms(), "uptime_s": self.uptime_s()})
            except Exception:  # noqa: BLE001
                pass
            self._bg_stop.wait(poll)

    def stop(self) -> None:
        self._bg_stop.set()
        self.radio.stop()
        self.relay.stop()
        self.pixhawk.stop()
        self._stopped = True
        log.info("context stopped")

    def uptime_s(self) -> int:
        return int(time.monotonic() - getattr(self, "_t0", time.monotonic()))

    # -------------------------------------------------------------- apply hook
    def register_config_hook(self) -> None:
        def _apply(rev: int) -> None:
            self.radio.reconfigure()
            self.links.reconfigure()
        self.config.on_change(_apply)

    # ------------------------------------------------------------- aggregates
    def system_health(self) -> dict:
        import os
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
        return {
            "components": dict(self.component_state),
            "config_revision": self.config.revision,
            "node": self.config.get("system.name", "relay"),
            "uptime_s": self.uptime_s(),
            "store_stats": self.store.stats(),
            "device": self.radio.device_status(),
            "relay": self.relay.snapshot(),
            "cpu_load": list(load),
            "pixhawk_connected": (self.pixhawk.connected if
                                  self.config.get("flight.enabled", False) else False),
        }
