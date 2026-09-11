"""Persistent store-and-forward queue backed by SQLite.

Survives temporary link loss and application restarts by design: queued
messages live on disk, not in RAM. All status transitions go through this
repository so delivery state is durable and consistent across processes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core.models import Message, MessageState, iso_now
from ..util.logger import get_logger

log = get_logger("store")

TERMINAL = {MessageState.DELIVERED, MessageState.FAILED, MessageState.EXPIRED, MessageState.DROPPED}
PENDING = {MessageState.QUEUED, MessageState.RETRYING}


class MessageStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init()

    # ------------------------------------------------------------------ setup
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=20)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    origin TEXT NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    payload BLOB,
                    payload_type TEXT,
                    clip_center_hz INTEGER NOT NULL DEFAULT 0,
                    clip_rate_hz INTEGER NOT NULL DEFAULT 2000000,
                    clip_duration_s REAL NOT NULL DEFAULT 0,
                    clip_peak_dbm REAL,
                    received_at_ms INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_ms INTEGER,
                    next_attempt_ms INTEGER,
                    delivered_at_ms INTEGER,
                    ttl_ms INTEGER,
                    delivery_ack TEXT,
                    created_at TEXT,
                    dup_fp TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_status ON messages(status);
                CREATE INDEX IF NOT EXISTS idx_dest ON messages(destination);
                CREATE INDEX IF NOT EXISTS idx_received ON messages(received_at_ms);
                CREATE INDEX IF NOT EXISTS idx_dup ON messages(dup_fp);
                CREATE TABLE IF NOT EXISTS counters (
                    channel TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (channel, metric)
                );
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first deployed schema so an existing
        queue DB (e.g. from an earlier model) upgrades in place, no data loss."""
        have = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        adds = {
            "clip_center_hz": "INTEGER NOT NULL DEFAULT 0",
            "clip_rate_hz": "INTEGER NOT NULL DEFAULT 2000000",
            "clip_duration_s": "REAL NOT NULL DEFAULT 0",
            "clip_peak_dbm": "REAL",
        }
        for col, ddl in adds.items():
            if col not in have:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {ddl}")

    # ---------------------------------------------------------------- messages
    @staticmethod
    def _row_to_msg(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"], origin=row["origin"], seq=row["seq"],
            source=row["source"], destination=row["destination"],
            payload=bytes(row["payload"] or b""), payload_type=row["payload_type"],
            clip_center_hz=row["clip_center_hz"], clip_rate_hz=row["clip_rate_hz"],
            clip_duration_s=row["clip_duration_s"], clip_peak_dbm=row["clip_peak_dbm"],
            received_at_ms=row["received_at_ms"], priority=row["priority"],
            status=MessageState(row["status"]), retry_count=row["retry_count"],
            last_attempt_ms=row["last_attempt_ms"], next_attempt_ms=row["next_attempt_ms"],
            delivered_at_ms=row["delivered_at_ms"], ttl_ms=row["ttl_ms"],
            delivery_ack=row["delivery_ack"],
        )

    def insert(self, m: Message) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages
                   (id,origin,seq,source,destination,payload,payload_type,
                    clip_center_hz,clip_rate_hz,clip_duration_s,clip_peak_dbm,
                    received_at_ms,priority,status,retry_count,last_attempt_ms,
                    next_attempt_ms,delivered_at_ms,ttl_ms,delivery_ack,created_at,dup_fp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (m.id, getattr(m, "origin", m.id), getattr(m, "seq", 0),
                 m.source, m.destination, m.payload, m.payload_type,
                 m.clip_center_hz, m.clip_rate_hz, m.clip_duration_s, m.clip_peak_dbm,
                 m.received_at_ms, m.priority, m.status.value, m.retry_count,
                 m.last_attempt_ms, m.next_attempt_ms, m.delivered_at_ms,
                 m.ttl_ms, m.delivery_ack, iso_now(),
                 getattr(m, "dup_fp", None)),
            )

    def get(self, message_id: str) -> Message | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        return self._row_to_msg(row) if row else None

    def find_duplicate(self, dup_fp: str) -> bool:
        if not dup_fp:
            return False
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE dup_fp=? AND status NOT IN "
                "('DELIVERED','FAILED','EXPIRED','DROPPED') LIMIT 1", (dup_fp,)).fetchone()
        return row is not None

    def update_status(self, message_id: str, status: MessageState, **extra: Any) -> None:
        with self._lock, self._conn() as conn:
            fields = {"status": status.value}
            fields.update(extra)
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE messages SET {sets} WHERE id=?", (*fields.values(), message_id))

    def claim_next(self, destination: str, now_ms: int,
                   terminal: Iterable[MessageState] = TERMINAL) -> Message | None:
        """Atomically claim the highest-priority due message for a destination."""
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM messages WHERE destination=? AND status IN ('QUEUED','RETRYING')
                   AND (next_attempt_ms IS NULL OR next_attempt_ms <= ?)
                   ORDER BY priority DESC, received_at_ms ASC LIMIT 1""",
                (destination, now_ms)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE messages SET status='TRANSMITTING', last_attempt_ms=?, next_attempt_ms=? "
                "WHERE id=?",
                (now_ms, now_ms, row["id"]))
            conn.commit()
            return self._row_to_msg(row)

    def ack_delivered(self, message_id: str, now_ms: int, ack: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE messages SET status='DELIVERED', delivered_at_ms=?, "
                "next_attempt_ms=NULL, delivery_ack=? WHERE id=?",
                (now_ms, ack, message_id))
            row = conn.execute("SELECT source,destination FROM messages WHERE id=?",
                               (message_id,)).fetchone()
        if row:
            self._bump(row["source"], "deliveries_ok")
            self._bump(row["destination"], "deliveries_ok")

    def fail_and_retry(self, message_id: str, now_ms: int, retry_interval_s: float,
                       backoff: float, max_interval_s: float, max_retries: int,
                       ttl_ms: int | None, destination: str) -> str:
        """Return the resulting state after applying a failed transmit attempt."""
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return "DROPPED"
            retries = row["retry_count"] + 1
            # expiry check
            expired = False
            if ttl_ms and now_ms - row["received_at_ms"] > ttl_ms:
                expired = True
            if expired or retries >= max_retries:
                new_status = "EXPIRED" if expired else "FAILED"
                conn.execute(
                    "UPDATE messages SET status=?, retry_count=?, next_attempt_ms=NULL WHERE id=?",
                    (new_status, retries, message_id))
                conn.commit()
                self._bump(destination, "deliveries_fail")
                return new_status
            interval = min(retry_interval_s * (backoff ** (retries - 1)), max_interval_s)
            next_ms = int(now_ms + interval * 1000)
            conn.execute(
                "UPDATE messages SET status='RETRYING', retry_count=?, next_attempt_ms=? "
                "WHERE id=?",
                (retries, next_ms, message_id))
            conn.commit()
            return "RETRYING"

    def expire_due(self, now_ms: int) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE messages SET status='EXPIRED'
                   WHERE status IN ('QUEUED','RETRYING') AND ttl_ms IS NOT NULL
                   AND (? - received_at_ms) > ttl_ms""", (now_ms,))
            return cur.rowcount

    def purge_terminal(self, older_than_ms: int) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """DELETE FROM messages WHERE status IN ('DELIVERED','FAILED','EXPIRED','DROPPED')
                   AND (COALESCE(delivered_at_ms, last_attempt_ms, received_at_ms)) < ?""",
                (older_than_ms,))
            return cur.rowcount

    def clear_expired_failed(self) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM messages WHERE status IN ('EXPIRED','FAILED','DROPPED')")
            return cur.rowcount

    # ---------------------------------------------------------------- queries
    def pending_count(self) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE status IN ('QUEUED','RETRYING')").fetchone()
        return int(row["c"])

    def destination_queue_len(self, destination: str) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE destination=? AND status IN "
                "('QUEUED','RETRYING')", (destination,)).fetchone()
        return int(row["c"])

    def list_messages(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM messages
                   ORDER BY (status IN ('QUEUED','RETRYING')) DESC, received_at_ms DESC
                   LIMIT ?""", (limit,)).fetchall()
        return [self._row_to_msg(r).to_dict() for r in rows]

    def queue_view(self) -> list[dict[str, Any]]:
        """Dashboard store-and-forward view (non-terminal first)."""
        order = {"QUEUED": 0, "RETRYING": 1, "TRANSMITTING": 2, "RECEIVED": 3,
                 "DELIVERED": 4, "FAILED": 5, "EXPIRED": 6, "DROPPED": 7}
        msgs = self.list_messages(limit=500)
        msgs.sort(key=lambda m: (order.get(m["status"], 9), m["received_at_ms"]))
        return msgs

    def stats(self) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) c FROM messages GROUP BY status").fetchall()
            counters = {
                (r["channel"], r["metric"]): r["value"]
                for r in conn.execute("SELECT * FROM counters")
            }
        return {
            "by_status": {r["status"]: r["c"] for r in rows},
            "counters": counters,
            "pending": self.pending_count(),
        }

    # --------------------------------------------------------------- counters
    def _bump(self, key: str, metric: str, delta: int = 1) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO counters(channel,metric,value) VALUES(?,?,?)
                   ON CONFLICT(channel,metric) DO UPDATE SET value=value+?""",
                (key, metric, delta, delta))

    def counter(self, channel: str, metric: str) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM counters WHERE channel=? AND metric=?",
                (channel, metric)).fetchone()
        return int(row["value"]) if row else 0

    def reset_counters(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM counters")

    # ------------------------------------------------------------ diagnostics
    def dump_json(self, path: Path, limit: int = 500) -> int:
        data = self.list_messages(limit)
        path.write_text(json.dumps(data, indent=2))
        return len(data)
