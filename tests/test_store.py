import time

from airrelay.core.models import Message, MessageState, utcnow_ms
from airrelay.store.queue_db import MessageStore
from airrelay.util.paths import resolve_paths


def _store(home):
    return MessageStore(resolve_paths(home).db_path)


def test_queue_roundtrip(home):
    st = _store(home)
    m = Message(source="F1", destination="F2", payload=b"hi", dup_fp="100",
                status=MessageState.QUEUED)
    st.insert(m)
    assert st.get(m.id).payload == b"hi"
    assert st.get(m.id).source == "F1"


def test_claim_and_deliver(home):
    st = _store(home)
    m = Message(source="F1", destination="F2", payload=b"x", dup_fp="1",
                status=MessageState.QUEUED)
    st.insert(m)
    claimed = st.claim_next("F2", utcnow_ms())
    assert claimed is not None
    assert st.get(m.id).status == MessageState.TRANSMITTING
    st.ack_delivered(m.id, utcnow_ms())
    assert st.get(m.id).status == MessageState.DELIVERED
    # no longer claimable
    assert st.claim_next("F2", utcnow_ms()) is None


def test_retry_backoff_and_fail(home):
    st = _store(home)
    m = Message(source="F1", destination="F3", payload=b"y", dup_fp="2",
                status=MessageState.QUEUED)
    st.insert(m)
    st.claim_next("F3", utcnow_ms())
    # simulate failures up to retry limit
    result = None
    for _ in range(10):
        result = st.fail_and_retry(m.id, utcnow_ms(), 1, 2.0, 300, 3,
                                   900_000, "F3")
        if result in ("FAILED", "EXPIRED"):
            break
    assert result == "FAILED"
    assert st.get(m.id).status == MessageState.FAILED
    assert st.get(m.id).retry_count >= 3


def test_expiry(home):
    st = _store(home)
    m = Message(source="F1", destination="F4", payload=b"z", dup_fp="3",
                status=MessageState.QUEUED, ttl_ms=100,
                received_at_ms=utcnow_ms() - 2000)
    st.insert(m)
    st.expire_due(utcnow_ms())
    assert st.get(m.id).status == MessageState.EXPIRED


def test_survives_restart(home):
    paths = resolve_paths(home)
    st1 = MessageStore(paths.db_path)
    m = Message(source="F1", destination="F2", payload=b"persist", dup_fp="9",
                status=MessageState.QUEUED)
    st1.insert(m)
    # simulate application restart: new store instance on same db file
    st2 = MessageStore(paths.db_path)
    got = st2.get(m.id)
    assert got is not None and got.payload == b"persist"
    assert st2.pending_count() == 1


def test_duplicate_detection(home):
    st = _store(home)
    m = Message(source="F1", destination="F2", payload=b"d", dup_fp="t1",
                status=MessageState.QUEUED)
    st.insert(m)
    assert st.find_duplicate("t1") is True
    assert st.find_duplicate("t2") is False


def test_old_schema_migrates_in_place(home):
    """A DB created under the older (pre-clip) schema must upgrade and remain
    usable when opened by the current MessageStore."""
    import sqlite3
    from airrelay.util.paths import resolve_paths
    paths = resolve_paths(home)
    db = paths.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, origin TEXT, seq INTEGER DEFAULT 0,
            source TEXT, destination TEXT, payload BLOB, payload_type TEXT,
            received_at_ms INTEGER, priority INTEGER DEFAULT 0,
            status TEXT, retry_count INTEGER DEFAULT 0,
            last_attempt_ms INTEGER, next_attempt_ms INTEGER,
            delivered_at_ms INTEGER, ttl_ms INTEGER, delivery_ack TEXT,
            created_at TEXT, dup_fp TEXT);
        INSERT INTO messages (id,source,destination,status) VALUES
            ('legacy1','F1','F2','QUEUED');
    """)
    conn.commit(); conn.close()
    st = MessageStore(db)  # triggers migration
    m = st.get('legacy1')
    assert m is not None and m.source == 'F1'
    # now inserts with clip columns succeed
    mm = Message(source='F1', destination='F3', payload=b'\x00\x01\x02\x03',
                 status=MessageState.QUEUED, clip_center_hz=145500000)
    st.insert(mm)
    assert st.get(mm.id).clip_center_hz == 145500000
