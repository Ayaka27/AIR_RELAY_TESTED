"""Automatic altitude optimization (advisory only).

The optimizer observes link quality, coverage and stability and produces a
SUGGESTED altitude (up / down / hold) for the human pilot or a guided ground
station. It never commands the flight controller directly — Pixhawk safety
(and operator authority) are preserved by design.

Because true coverage/RSSI-vs-altitude telemetry is sparse and noisy in the
field, the controller uses deliberate hysteresis, minimum stabilization
dwell and a cooldown to avoid constant altitude oscillation:

    state = MONITORING | OPTIMIZATION | HOLD | RECOVERY | MANUAL

Each evaluation:
  1. Feasibility gates: telemetry fresh, within min/max altitude, link data
     present. If any gate fails -> RECOVERY (or MANUAL if disabled) and HOLD.
  2. Compute a weighted score from reachable-channel fraction, mean RSSI and
     SNR, and delivery success rate.
  3. Request an altitude step only when score has been persistently below
     target for >= stabilization period (down) or clearly better (up), then
     enter HOLD + cooldown so the vehicle and RF environment settle.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..core.event_bus import EventBus
from ..core.models import AltState, AltitudeSuggestion, utcnow_ms
from ..util.logger import get_logger

log = get_logger("altitude")

NO_TELEMETRY = 0
OK = 1


class AltitudeOptimizer:
    def __init__(self, config, links, pixhawk, bus: EventBus) -> None:
        self.cfg = config
        self.links = links
        self.pixhawk = pixhawk
        self.bus = bus
        self._lock = threading.RLock()
        self._state = AltState.MONITORING
        self._suggestion = AltitudeSuggestion(state=AltState.MONITORING)
        self._last_alt: Optional[float] = None
        self._score_history: list[float] = []
        self._state_entered_ms = utcnow_ms()
        self._last_change_ms = 0

    # ----------------------------------------------------------------- gates
    def enabled(self) -> bool:
        return bool(self.cfg.get("altitude.enabled", False))

    def _alt_limits(self):
        return (float(self.cfg.get("altitude.min_alt_m", 50)),
                float(self.cfg.get("altitude.max_alt_m", 400)))

    def _feasible(self, alt: Optional[float]) -> bool:
        if not self.pixhawk or not self.pixhawk.connected:
            return False
        if alt is None:
            return False
        lo, hi = self._alt_limits()
        if not (lo <= alt <= hi):
            return False
        return True

    # ----------------------------------------------------------------- score
    def _metrics(self):
        chans = self.links.all()
        enabled = [c for c in chans if self.cfg.channel(c.channel_id) and
                   self.cfg.channel(c.channel_id).enabled]
        if not enabled:
            return 0.0, 0.0, 0.0, 0.0, 0
        # A channel counts as reachable when it has been heard recently and is
        # not silently absent (only meaningful when far ends beacon/probe).
        now = utcnow_ms()
        deg_ms = int(self.cfg.get("link_monitor.degraded_timeout_ms", 20000))
        reachable = [
            (c.last_rx_at_ms is not None) and
            (now - (c.last_rx_at_ms or 0) <= deg_ms)
            for c in enabled]
        coverage = sum(1 for r in reachable if r) / max(1, len(enabled))
        rssis = [c.last_rssi_dbm for c in enabled if c.last_rssi_dbm is not None]
        snrs = [c.last_snr_db for c in enabled if c.last_snr_db is not None]
        mean_rssi = sum(rssis) / len(rssis) if rssis else float("-inf")
        mean_snr = sum(snrs) / len(snrs) if snrs else float("-inf")
        ok = sum(c.deliveries_ok for c in enabled)
        fail = sum(c.deliveries_fail for c in enabled)
        success = ok / (ok + fail) if (ok + fail) else 1.0
        return coverage, mean_rssi, mean_snr, success, len(enabled)

    def _score(self):
        w = self.cfg.get("altitude.score", {})
        cov, rssi, snr, success, n = self._metrics()
        s = 0.0
        # coverage normalized already 0..1
        s += float(w.get("w_coverage", 2.0)) * cov
        # rssi: better (less negative) -> higher. target_dbm scales to 0..1
        tgt = float(w.get("rssi_target_dbm", -85.0))
        if rssi > float("-inf"):
            rssi_score = max(0.0, min(1.0, (rssi - (tgt - 30)) / (tgt - (tgt - 30))))
            s += float(w.get("w_rssi", 1.0)) * rssi_score
        if snr > float("-inf"):
            snr_score = max(0.0, min(1.0, snr / float(w.get("snr_target_db", 12.0))))
            s += float(w.get("w_snr", 1.0)) * snr_score
        s += float(w.get("w_stability", 1.0)) * success
        # altitude cost: favour staying low when quality already sufficient
        alt = self._last_alt or 0
        _, hi = self._alt_limits()
        s -= float(w.get("w_altitude_cost", 0.5)) * (alt / max(1, hi))
        return max(0.0, s)

    # ----------------------------------------------------------------- main
    def step(self) -> AltitudeSuggestion:
        with self._lock:
            self._last_alt = (self.pixhawk.telemetry() or {}).get("alt_rel_m") if self.pixhawk else None
        lo, hi = self._alt_limits()
        sug = AltitudeSuggestion()
        if not self.enabled():
            sug.state = AltState.MANUAL
            sug.reason = "altitude optimization disabled"
            sug.suggested_alt_m = None
            self._state = AltState.MANUAL
            self._suggestion = sug
            self.bus.publish("altitude.suggestion", sug.to_dict())
            return sug
        alt = self._last_alt
        feasible = self._feasible(alt)
        now_ms = utcnow_ms()
        if not feasible:
            self._state = AltState.RECOVERY
            sug.state = AltState.RECOVERY
            sug.current_alt_m = alt
            sug.reason = ("no fresh telemetry / out of limits — holding" if
                          (self.pixhawk and not self.pixhawk.connected) else
                          "telemetry stale or altitude outside limits")
            self._suggestion = sug
            self.bus.publish("altitude.suggestion", sug.to_dict())
            return sug
        score = self._score()
        self._score_history.append(score)
        if len(self._score_history) > 20:
            self._score_history.pop(0)
        below_target = score < 3.0  # persistent poor link
        stab_s = float(self.cfg.get("altitude.stabilization_s", 20))
        cool_s = float(self.cfg.get("altitude.cooldown_s", 30))
        in_stab = now_ms - self._state_entered_ms >= stab_s * 1000
        cooled = now_ms - self._last_change_ms >= cool_s * 1000
        hyst = float(self.cfg.get("altitude.hysteresis_m", 15))
        step = float(self.cfg.get("altitude.alt_step_m", 20))

        action = "hold"
        if cooled and in_stab and self._state == AltState.MONITORING:
            # For an airborne relay, raising the platform is the effective way
            # to restore line-of-sight coverage when the link is poor and head
            # room remains. Descending is intentionally conservative and only
            # offered when quality is strong and we are well above the floor,
            # gated by hysteresis to avoid oscillation.
            if below_target and alt < hi - hyst:
                action = "up"
            elif (not below_target) and alt > lo + hyst and score > 4.5:
                action = "down"
        sug.score = score
        sug.current_alt_m = alt
        if action == "up":
            target = min(hi, alt + step)
            self._state = AltState.HOLD
            self._state_entered_ms = now_ms
            self._last_change_ms = now_ms
            sug.suggested_alt_m = target
            sug.reason = (f"poor link (score {score:.2f}); suggest climbing "
                          f"to ~{target:.0f} m for better LOS")
            log.info("altitude suggestion: climb", target=target, score=score)
        elif action == "down":
            target = max(lo, alt - step)
            self._state = AltState.HOLD
            self._state_entered_ms = now_ms
            self._last_change_ms = now_ms
            sug.suggested_alt_m = target
            sug.reason = (f"strong link (score {score:.2f}); may descend to "
                          f"~{target:.0f} m to conserve power/altitude")
            log.info("altitude suggestion: descend", target=target, score=score)
        elif self._state == AltState.HOLD and cooled:
            self._state = AltState.MONITORING
            self._state_entered_ms = now_ms
            sug.suggested_alt_m = alt
            sug.reason = "hold elapsed; resuming monitoring"
        else:
            if self._state != AltState.HOLD:
                self._state = AltState.MONITORING
            sug.suggested_alt_m = None
            sug.reason = f"monitoring (score {score:.2f})"
        sug.state = self._state
        self._suggestion = sug
        self.bus.publish("altitude.suggestion", sug.to_dict())
        return sug

    def suggestion(self) -> AltitudeSuggestion:
        return self._suggestion
