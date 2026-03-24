# Bug Fix Plan: Replay Rejection on First Message

**Date:** 2026-03-24
**Status:** Fixed
**Priority:** High (Test Failures)
**Affected Tests:** 4 integration tests (all 4 previously failing)

---

## 1. Problem Summary

Four integration tests were failing with `409 Conflict - "Duplicate message (replay rejected)"` when sending the **first message** (counter=0) in a conversation:

- `test_full_e2e_message_flow`
- `test_replay_rejection`
- `test_offline_queue_store_and_forward`
- `test_offline_queue_replay_rejected`

The error message was misleading — the messages were not actual replays. The real root cause was a **type mismatch** between the wire protocol and the database model, combined with a overly-broad exception handler.

---

## 2. Root Cause Analysis

### Primary Cause: `int` vs `datetime` Type Mismatch

The wire protocol (`MessageEnvelope`) defines `sent_at` as `int` (unix timestamp):

```python
# shared/protocol.py
class MessageEnvelope(BaseModel):
    sent_at: int  # unix timestamp (client clock)
```

But the SQLAlchemy ORM model (`Message`) defines `sent_at` as `datetime`:

```python
# server/models/message.py
class Message(Base):
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
```

When the server created a `Message` object from the envelope:

```python
# server/api/messages.py (BEFORE fix)
message = Message(
    ...
    sent_at=env.sent_at,  # env.sent_at is int, but Message.sent_at expects datetime!
)
```

SQLAlchemy raised a type error during `db.flush()`. The same issue occurred with `conv.last_message_at = env.sent_at`.

### Secondary Cause: Overly-Broad Exception Handler

The `send_message` endpoint caught **all** exceptions and treated them as replay attempts:

```python
try:
    ...
    await db.flush()
except Exception:  # ← Catches EVERYTHING, not just UNIQUE constraint violations
    log.warning("replay_rejected", ...)
    raise HTTPException(status_code=409, detail="Duplicate message (replay rejected)")
```

This masked the real type error, making debugging extremely difficult.

### Tertiary Cause: Stale Test Database Files

The integration test fixtures used file-based SQLite databases (`/tmp/test_e2e.db`, `/tmp/test_offline.db`) without cleaning them between test runs. While this wasn't the primary cause of the failures (the type error would fail even on a fresh DB), it compounded the issue by potentially leaving stale data.

---

## 3. Fix Applied

### Fix 1: Convert `int` timestamps to `datetime` in `server/api/messages.py`

Added `from datetime import datetime` import and converted timestamps at the boundary:

```python
# When creating Message from envelope (int -> datetime)
sent_at_dt = datetime.fromtimestamp(env.sent_at)
message = Message(
    ...
    sent_at=sent_at_dt,
)
conv.last_message_at = sent_at_dt

# When creating MessageEnvelope from Message (datetime -> int)
sent_at=int(msg.sent_at.timestamp()) if isinstance(msg.sent_at, datetime) else msg.sent_at

# When setting delivered_at
now = datetime.fromtimestamp(int(time.time()))
msg.delivered_at = now
```

### Fix 2: Clean test database files before each test run

Added `os.unlink()` before `init_db()` in both integration test fixtures:

```python
# tests/integration/test_e2e_message.py
try:
    os.unlink("/tmp/test_e2e.db")
except FileNotFoundError:
    pass

# tests/integration/test_offline_queue.py
try:
    os.unlink("/tmp/test_offline.db")
except FileNotFoundError:
    pass
```

---

## 4. Files Modified

| File                                                                                 | Change                                                                                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| [`server/api/messages.py`](server/api/messages.py)                                   | Added `datetime` import; converted `int` ↔ `datetime` for `sent_at`, `last_message_at`, `delivered_at` |
| [`tests/integration/test_e2e_message.py`](tests/integration/test_e2e_message.py)     | Added DB file cleanup before `init_db()`                                                               |
| [`tests/integration/test_offline_queue.py`](tests/integration/test_offline_queue.py) | Added DB file cleanup before `init_db()`                                                               |

---

## 5. Verification

```
$ uv run --extra dev pytest tests/ --tb=short
53 passed in 2.86s
```

All 53 tests pass, including the 4 previously failing integration tests.

---

## 6. Lessons Learned

1. **Narrow exception handlers**: The `except Exception` clause should be replaced with specific exception types (e.g., `sqlalchemy.exc.IntegrityError`) to avoid masking real errors.

2. **Type consistency at boundaries**: When ORM models use `DateTime` but wire protocol uses `int` timestamps, explicit conversion must happen at the API boundary layer.

3. **Test isolation**: Test fixtures should always start with a clean state. Using in-memory SQLite (`sqlite+aiosqlite:///:memory:`) would be more robust than file-based databases.

---

## 7. Recommended Follow-Up

- [ ] Replace `except Exception` with `except IntegrityError` in [`server/api/messages.py`](server/api/messages.py:155) to catch only UNIQUE constraint violations
- [ ] Consider using in-memory SQLite for integration tests to eliminate file cleanup issues
- [ ] Add type conversion utility functions to avoid scattered `datetime.fromtimestamp()` / `.timestamp()` calls
