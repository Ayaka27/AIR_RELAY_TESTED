# Store-and-forward

## 1. Durability principle

Queued messages live in **SQLite on disk** (`data/store.sqlite3`), not in RAM.
Temporary link loss **and** application restarts never lose the queue. Each
message carries full metadata so delivery state is auditable.

## 2. Message metadata

```jsonc
{
  "id": "…16 hex…",          // unique
  "origin": "…",             // originating node token seed
  "source": "F1",            // routing source channel
  "destination": "F4",       // routing destination channel
  "payload_bytes": n,        // opaque bytes
  "received_at_ms": …,
  "priority": 0,             // higher = claimed first
  "status": "QUEUED",
  "retry_count": 0,
  "last_attempt_ms": null,
  "next_attempt_ms": null,
  "delivered_at_ms": null,
  "ttl_ms": 900000,          // expiration
  "delivery_ack": null,      // how it was confirmed
  "dup_fp": "token",         // loop/duplicate suppression key
  "age_ms": …
}
```

## 3. States

`RECEIVED → QUEUED → TRANSMITTING → DELIVERED`, with `RETRYING` between
attempts, and terminal `FAILED`, `EXPIRED`, `DROPPED`. Messages are inserted
directly as `QUEUED` at routing time. See the lifecycle diagram in
`architecture.md`.

## 4. Delivery logic (store `claim_next`)

* Only `QUEUED`/`RETRYING` messages whose `next_attempt_ms` is due are
  claimable.
* Claims are ordered by `priority DESC, received_at ASC`.
* A claim atomically sets `TRANSMITTING` (so a single message is only ever
  transmitted once at a time).

## 5. Retry / backoff

On a failed attempt `store.fail_and_retry` computes:

```python
interval = min(retry_interval_s * backoff_factor**(retries-1), max_retry_interval_s)
next_attempt_ms = now + interval
```

and either schedules a retry (`RETRYING`), or terminates the message when the
retry limit is reached (`FAILED`) or its TTL is exceeded (`EXPIRED`). This is
bounded and never transmits the same message uncontrolled.

## 6. Expiry & retention

* `store_forward.ttl_s` — a message older than this while still queued is
  expired.
* `store_forward.delivered_retention_s` — delivered/terminal rows are purged
  after this (dashboard history window).
* Operator can clear `EXPIRED/FAILED/DROPPED` from the dashboard.

## 7. Queue limits

* `store_forward.max_queue_size` caps pending messages (dashboard/relay respect
  it; exceeded arrivals are dropped with `DROPPED` semantics at the ingest
  boundary if configured).

## 8. Delivery tracking

Per-channel counters (`deliveries_ok`, `deliveries_fail`) are kept in SQLite
and surfaced on the link monitor / queue view.

## 9. Concurrency

The store is guarded by a threading lock and uses SQLite WAL mode with bounded
transactions, so the relay loop and the web API can operate on the queue
concurrently without corruption.
