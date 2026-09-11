import pytest

from airrelay.web.app import create_app
from airrelay.web.context import AppContext


@pytest.fixture()
def client(home):
    ctx = AppContext(home=home)
    app = create_app(ctx)
    app.testing = True
    with app.test_client() as c:
        c.ctx = ctx
        yield c


def test_overview(client):
    r = client.get("/api/overview")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert len(j["channels"]) == 4


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_invalid_channel_config_rejected(client):
    bad = [{"id": "F1", "frequency_mhz": 145.5, "enabled": True},
           {"id": "F2", "frequency_mhz": 145.5, "enabled": True}]
    r = client.put("/api/config/channels", json={"channels": bad})
    assert r.status_code == 400
    assert r.get_json()["applied"] is False


def test_valid_channel_config_applied(client):
    good = [{"id": "F1", "frequency_mhz": 144.390, "enabled": True,
             "tx_allowed": True, "rx_allowed": True},
            {"id": "F2", "frequency_mhz": 432.600, "enabled": True,
             "tx_allowed": True, "rx_allowed": True}]
    r = client.put("/api/config/channels", json={"channels": good})
    assert r.status_code == 200
    j = r.get_json()
    assert j["applied"] is True
    got = {c["id"]: c["frequency_mhz"] for c in j["channels"]}
    assert got["F1"] == 144.390


def test_malformed_request(client):
    r = client.put("/api/config/channels", json={"channels": "nope"})
    assert r.status_code == 400


def test_messages_and_clear(client):
    assert client.get("/api/messages").status_code == 200
    r = client.post("/api/queue/clear_expired_failed")
    assert r.status_code == 200


def test_runtime_config_update(client):
    r = client.put("/api/config", json={"radio.tx_enable": False})
    assert r.status_code == 200
    assert "radio.tx_enable" in r.get_json()["changed"]
    # unknown key silently ignored
    r2 = client.put("/api/config", json={"radio.tx_enable": True, "evil": 1})
    assert r2.status_code == 200


def test_auth_status(client):
    assert client.get("/api/auth_status").get_json()["enabled"] is False


def test_inject_when_allowed(client):
    client.ctx.config.set("dev.allow_inject", True)
    r = client.post("/api/test/inject", json={"channel": "F1", "payload": "probe"})
    assert r.status_code == 200


def test_full_context_start_and_stop(home):
    """Exercise the background relay/radio/context loops (thread path)."""
    import time
    ctx = AppContext(home=home)
    ctx.register_config_hook()
    ctx.start()
    time.sleep(1.0)
    assert ctx.system_health()["components"]["relay"] is True
    assert ctx.store.pending_count() == 0
    ctx.stop()
    assert ctx.system_health() is not None
