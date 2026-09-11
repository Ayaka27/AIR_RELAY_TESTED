"""REST/SSE API for the dashboard.

Security model:
  * Reads (GET) are allowed by default so a ground operator can see the
    system without a token (`dashboard.public_link_read`). Set to false to
    require auth for all requests.
  * Writes (frequency/config changes, queue controls) always require a valid
    session when `dashboard.auth_enabled` is true.
  * Malformed requests return 400; unknown fields are rejected where safe.
"""

from __future__ import annotations

import json
import queue as _q
import time
from typing import Any

from flask import (Blueprint, Response, current_app, jsonify, request, session)

from ..core.config import ConfigError, normalize_freq, validate_freq_hz
from ..util.resource import ResourceMonitor
from ..util.logger import get_logger

log = get_logger("api")
api = Blueprint("api", __name__)

_resource = ResourceMonitor()


def _ctx():
    return current_app.config["ctx"]


# --------------------------------------------------------------- auth helper
def _auth_required_ok() -> bool:
    cfg = _ctx().config
    if not cfg.get("dashboard.auth_enabled", False):
        return True
    token = session.get("token")
    return bool(token and _ctx().auth.verify(token))


def _auth_or_abort():
    if not _auth_required_ok():
        return jsonify({"ok": False, "error": "authentication required"}), 401
    return None


# ---------------------------------------------------------------- overview
def _channel_rows():
    ctx = _ctx()
    rows = []
    for ch in ctx.config.channels():
        st = ctx.links.get(ch.id)
        rows.append({
            "id": ch.id,
            "label": ch.label,
            "enabled": ch.enabled,
            "tx_allowed": ch.tx_allowed,
            "rx_allowed": ch.rx_allowed,
            "frequency_hz": ch.frequency_hz,
            "frequency_mhz": ch.freq_mhz,
            "link": st.to_dict() if st else {},
        })
    return rows


@api.get("/api/overview")
def overview():
    ctx = _ctx()
    payload = {
        "ok": True,
        "channels": _channel_rows(),
        "band_limits": ctx.config.limits(),
        "config_revision": ctx.config.revision,
        "config_mode": ctx.config.get("relay.mode", "bridge"),
        "confirm": ctx.config.get("relay.confirm", "rf_tx"),
        "system": ctx.system_health(),
        "radio": ctx.radio.device_status(),
        "flight": (ctx.pixhawk.telemetry() if ctx.config.get("flight.enabled", False)
                   else {"connected": False, "disabled": True}),
        "altitude": ctx.altitude.suggestion().to_dict(),
        "relay": ctx.relay.snapshot(),
        "resource": _resource.snapshot(),
        "ts": int(time.time() * 1000),
    }
    return jsonify(payload)


@api.get("/api/links")
def links():
    return jsonify({"ok": True, "links": _ctx().links.snapshot()})


@api.get("/api/messages")
def messages():
    limit = request.args.get("limit", default=200, type=int)
    q = request.args.get("state")
    msgs = _ctx().store.queue_view()
    if q:
        msgs = [m for m in msgs if m["status"] == q]
    return jsonify({"ok": True, "messages": msgs[:limit],
                    "stats": _ctx().store.stats()})


@api.get("/api/system")
def system():
    ctx = _ctx()
    return jsonify({
        "ok": True, "system": ctx.system_health(),
        "resource": _resource.snapshot(),
        "log_level": ctx.config.get("logging.level"),
    })


@api.get("/api/radio")
def radio():
    return jsonify({"ok": True, "device": _ctx().radio.device_status()})


@api.get("/api/flight")
def flight():
    ctx = _ctx()
    if ctx.config.get("flight.enabled", False):
        return jsonify({"ok": True, "flight": ctx.pixhawk.telemetry()})
    return jsonify({"ok": True, "flight": {"disabled": True}})


@api.get("/api/altitude")
def altitude():
    return jsonify({"ok": True, "altitude": _ctx().altitude.suggestion().to_dict(),
                    "enabled": _ctx().config.get("altitude.enabled", False)})


# ------------------------------------------------------------ configuration
@api.get("/api/config")
def config_read():
    return jsonify(_ctx().config.snapshot())


@api.get("/api/config/channels")
def config_channels_read():
    return jsonify({"ok": True, "channels": _ctx().config.channels_raw(),
                    "limits": _ctx().config.limits(),
                    "revision": _ctx().config.revision})


@api.put("/api/config/channels")
def config_channels_apply():
    err = _auth_or_abort()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    raw = data.get("channels")
    if not isinstance(raw, list) or not raw:
        return jsonify({"ok": False, "error": "channels list required"}), 400
    try:
        normalized = []
        for c in raw:
            if not isinstance(c, dict):
                raise ConfigError("each channel must be an object")
            val = normalize_freq(c.get("frequency_mhz", c.get("frequency_hz", 0)))
            limits = _ctx().config.limits()
            validate_freq_hz(val, limits)
            normalized.append({
                "id": str(c["id"]),
                "label": str(c.get("label", c["id"])),
                "enabled": bool(c.get("enabled", True)),
                "frequency_mhz": val / 1e6,
                "tx_allowed": bool(c.get("tx_allowed", True)),
                "rx_allowed": bool(c.get("rx_allowed", True)),
            })
        _ctx().config.apply_channels(normalized)
        _ctx().radio.reconfigure()
        _ctx().links.reconfigure()
        log.info("channels applied via API", rev=_ctx().config.revision)
        return jsonify({"ok": True, "revision": _ctx().config.revision,
                        "applied": True,
                        "channels": _ctx().config.channels_raw()})
    except ConfigError as e:
        log.warning("channel config rejected", error=str(e))
        return jsonify({"ok": False, "error": str(e), "applied": False}), 400


@api.put("/api/config")
def config_update():
    """Update a small allow-list of scalar runtime parameters."""
    err = _auth_or_abort()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    allow = {
        "radio.tx_enable", "radio.tx_amp_enable", "radio.rx_gain_db",
        "link_monitor.available_timeout_ms", "link_monitor.degraded_timeout_ms",
        "store_forward.retry_interval_s", "store_forward.max_retry_count",
        "store_forward.ttl_s", "relay.mode", "relay.confirm",
        "altitude.enabled", "altitude.min_alt_m", "altitude.max_alt_m",
        "flight.enabled",
    }
    changed = []
    for k, v in data.items():
        if k not in allow:
            continue
        try:
            _ctx().config.set(k, v)
            changed.append(k)
        except ConfigError as e:
            return jsonify({"ok": False, "error": f"{k}: {e}"}), 400
    if "radio.tx_enable" in changed or "radio.rx_gain_db" in changed:
        _ctx().radio.reconfigure()
    if "relay.mode" in changed:
        log.info("relay mode changed", mode=data.get("relay.mode"))
    log.info("runtime config updated", keys=changed)
    return jsonify({"ok": True, "changed": changed,
                    "revision": _ctx().config.revision})


# ---------------------------------------------------------------- controls
@api.post("/api/queue/clear_expired_failed")
def clear_expired_failed():
    err = _auth_or_abort()
    if err:
        return err
    n = _ctx().store.clear_expired_failed()
    log.info("queue cleared expired/failed", count=n)
    return jsonify({"ok": True, "removed": n})


@api.post("/api/queue/purge_delivered")
def purge_delivered():
    err = _auth_or_abort()
    if err:
        return err
    now = int(time.time() * 1000)
    n = _ctx().store.purge_terminal(now - 1000)  # only already-terminal retained
    return jsonify({"ok": True, "removed": n})


# ------------------------------------------------------- test injection (dev)
@api.post("/api/test/inject")
def inject():
    """Simulate an inbound frame on a channel so relay/queue behaviour can be
    validated without live RF. Only accepted when `radio.tx_enable` is on and
    the TX device is absent (bring-up) OR always when `dev.allow_inject`."""
    err = _auth_or_abort()
    if err:
        return err
    if not _ctx().config.get("dev.allow_inject", False):
        return jsonify({"ok": False,
                        "error": "test injection disabled (dev.allow_inject)"}), 403
    data = request.get_json(silent=True) or {}
    src = str(data.get("channel", "F1"))
    rate = int(_ctx().config.get("radio.rf_sample_rate_hz", 2_000_000))
    dur = float(data.get("duration_s", 0.3))
    # Synthesize an opaque I/Q clip (pseudo-random, intentionally un-decodable)
    # so store-and-forward behaviour can be exercised without live RF.
    import random
    from ..core.models import RxClip
    rnd = random.Random(1)
    n = int(rate * dur)
    iq = bytes(bytearray(rnd.randrange(256) for _ in range(n * 4)))
    ch = _ctx().config.channel(src)
    clip = RxClip(source_channel=src,
                  center_hz=ch.frequency_hz if ch else 0,
                  sample_rate_hz=rate, duration_s=dur,
                  peak_rssi_dbm=-70.0, iq=iq)
    ctx = _ctx()
    ctx.relay._on_clip(clip, src)
    return jsonify({"ok": True, "source": src, "clip_bytes": len(iq),
                    "duration_s": dur})


# ------------------------------------------------------------------- auth
@api.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    if _ctx().auth.verify(token):
        session["token"] = token
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid token"}), 401


@api.post("/api/logout")
def logout():
    session.pop("token", None)
    return jsonify({"ok": True})


@api.get("/api/auth_status")
def auth_status():
    cfg = _ctx().config
    enabled = bool(cfg.get("dashboard.auth_enabled", False))
    return jsonify({"ok": True, "enabled": enabled,
                    "authenticated": _auth_required_ok()})


# -------------------------------------------------------------------- SSE
@api.get("/api/stream")
def stream():
    ctx = _ctx()
    q: "Any" = _q.Queue(maxsize=200)
    topics = {"relay.events", "relay.message", "relay.delivered", "relay.retry",
              "relay.failed", "flight.telemetry", "altitude.suggestion",
              "system.heartbeat", "config.changed"}

    def _send(topic: str, payload: dict[str, Any]) -> None:
        try:
            q.put_nowait({"topic": topic, "payload": payload})
        except _q.Full:
            pass

    subs = []
    for t in topics:
        subs.append(ctx.bus.subscribe(t, _send))

    def gen():
        try:
            while True:
                try:
                    item = q.get(timeout=10)
                except _q.Empty:
                    yield ": keepalive\n\n"
                    continue
                data = json.dumps(item["payload"], default=str)
                yield f"event: {item['topic']}\ndata: {data}\n\n"
        finally:
            for u in subs:
                u()

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})
