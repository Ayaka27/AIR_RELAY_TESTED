from airrelay.core.config import ConfigManager
from airrelay.core.event_bus import EventBus
from airrelay.core.models import MessageState, RxClip, utcnow_ms
from airrelay.relay.engine import RelayEngine
from airrelay.relay.link_monitor import LinkMonitor
from airrelay.store.queue_db import MessageStore
from airrelay.util.paths import resolve_paths


class FakeRadio:
    def __init__(self):
        self.tx_present = True
        self.jobs = []
        self._clip = None
        self._tx_done = None

    def on_clip(self, cb):
        self._clip = cb

    def on_tx_done(self, cb):
        self._tx_done = cb

    def tx_dev_present(self):
        return self.tx_present

    def rx_dev_present(self):
        return False

    def enqueue_tx(self, dst, iq, freq, ref, rate_hz=2_000_000):
        self.jobs.append((dst, iq, ref))


def _engine(home, tx_present=True):
    paths = resolve_paths(home)
    cfg = ConfigManager(paths.config_file)
    store = MessageStore(paths.db_path)
    links = LinkMonitor(cfg)
    radio = FakeRadio()
    radio.tx_present = tx_present
    bus = EventBus()
    eng = RelayEngine(cfg, store, links, radio, bus)
    return cfg, store, eng, radio


def _inject(eng, src="F1", start=111, dur=0.2):
    clip = RxClip(source_channel=src, start_ms=start, center_hz=145_000_000,
                  sample_rate_hz=2_000_000, duration_s=dur,
                  peak_rssi_dbm=-70.0, iq=b"\x01\x02\x03\x04" * 1000)
    eng._on_clip(clip, src)


def test_fanout_all_others(home):
    cfg, store, eng, _ = _engine(home)
    _inject(eng, "F1", 101)
    dests = sorted(set(m["destination"] for m in store.queue_view()
                       if m["status"] in ("QUEUED", "TRANSMITTING")))
    assert dests == ["F2", "F3", "F4"]
    # payload is stored opaque, not decoded as text
    for m in store.queue_view():
        if m["destination"] == "F2":
            assert m["payload_bytes"] == 4000


def test_duplicate_clip_same_start_dropped(home):
    cfg, store, eng, _ = _engine(home)
    _inject(eng, "F1", 202)
    before = store.pending_count()
    _inject(eng, "F1", 202)  # same channel+start re-ingested
    assert store.pending_count() == before


def test_delivery_when_tx_present(home):
    cfg, store, eng, radio = _engine(home, tx_present=True)
    _inject(eng, "F1", 303)
    eng._flush_deliverable()
    assert len(radio.jobs) == 3
    for dst, iq, ref in radio.jobs:
        eng._on_tx_done(ref, True)
    states = {m["destination"]: m["status"] for m in store.queue_view()}
    assert all(v == MessageState.DELIVERED.value for v in states.values())
    assert len(states) == 3


def test_queues_when_tx_unavailable(home):
    cfg, store, eng, radio = _engine(home, tx_present=False)
    _inject(eng, "F1", 404)
    eng._flush_deliverable()
    assert radio.jobs == []
    states = {m["status"] for m in store.queue_view()}
    assert states == {MessageState.QUEUED.value}
    assert store.pending_count() == 3
    radio.tx_present = True
    eng._flush_deliverable()
    assert len(radio.jobs) == 3


def test_queueing_on_disabled_tx_channel(home):
    cfg, store, eng, radio = _engine(home, tx_present=True)
    chans = cfg.channels_raw()
    for c in chans:
        if c["id"] == "F2":
            c["tx_allowed"] = False
    cfg.apply_channels(chans)
    _inject(eng, "F1", 505)
    eng._flush_deliverable()
    dst_done = {d for (d, _iq, _r) in radio.jobs}
    assert "F2" not in dst_done
    assert "F3" in dst_done and "F4" in dst_done
    f2 = [m for m in store.queue_view() if m["destination"] == "F2"][0]
    assert f2["status"] == MessageState.QUEUED.value
