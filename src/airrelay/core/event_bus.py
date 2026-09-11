"""Thread-safe publish/subscribe event bus.

Subsystems publish structured snapshots/events here; the web dashboard and
logging sinks subscribe. Decouples producers (relay, radio, flight) from
consumers (HTTP/SSE layer).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable

Handler = Callable[[str, dict[str, Any]], None]


class EventBus:
    def __init__(self, retain: bool = True) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._retain = retain
        self._last: dict[str, dict[str, Any]] = {}

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._subs[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subs[topic].remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._retain:
                self._last[topic] = payload
            subs = list(self._subs.get(topic, []))
            wild = list(self._subs.get("*", []))
        for h in subs + wild:
            try:
                h(topic, payload)
            except Exception:  # noqa: BLE001 - a bad subscriber must not break producers
                pass

    def latest(self, topic: str) -> dict[str, Any] | None:
        with self._lock:
            return self._last.get(topic)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._last)
