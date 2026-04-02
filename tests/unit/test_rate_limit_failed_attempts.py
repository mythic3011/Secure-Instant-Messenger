"""tests/unit/test_rate_limit_failed_attempts.py — failed-attempt-only login limiter tests."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

import server.core.database as db_mod
from server.core.config import clear_settings_cache
from server.core.security import check_rate_limit, reset_rate_limit
from server.models.rate_limit import RateLimit

pytestmark = pytest.mark.anyio


@pytest.fixture
async def rate_limit_db(tmp_path, monkeypatch):
    """Initialise an isolated SQLite DB for each test."""
    db_path = tmp_path / "rate_limit_failed_attempts.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    clear_settings_cache()
    await db_mod.init_db()

    try:
        yield
    finally:
        await db_mod.close_db()
        clear_settings_cache()


async def _get_row(key: str) -> RateLimit | None:
    async with db_mod.get_session() as db:
        result = await db.execute(select(RateLimit).where(RateLimit.key == key))
        return result.scalar_one_or_none()


def _key() -> str:
    return f"login:test-{uuid.uuid4().hex}"


async def test_rate_limit_counts_failed_attempts_only(rate_limit_db):
    key = _key()

    # Successful checks do not increment counters.
    for _ in range(8):
        assert await check_rate_limit(key, max_attempts=5, window_seconds=300, is_failed_attempt=False) is True

    assert await _get_row(key) is None

    # Five failed attempts are still allowed; the next one is blocked.
    for _ in range(5):
        assert await check_rate_limit(key, max_attempts=5, window_seconds=300, is_failed_attempt=True) is True

    assert await check_rate_limit(key, max_attempts=5, window_seconds=300, is_failed_attempt=True) is False


async def test_successful_login_resets_counter(rate_limit_db):
    key = _key()

    for _ in range(3):
        assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True

    row = await _get_row(key)
    assert row is not None
    assert row.attempts == 3

    await reset_rate_limit(key)

    # Non-locked records are removed by reset.
    assert await _get_row(key) is None


async def test_failed_attempts_after_reset(rate_limit_db):
    key = _key()

    for _ in range(2):
        assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True

    await reset_rate_limit(key)

    for _ in range(2):
        assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True

    row = await _get_row(key)
    assert row is not None
    assert row.attempts == 2


async def test_lockout_period_respected(rate_limit_db):
    key = _key()

    assert await check_rate_limit(key, 2, 300, is_failed_attempt=True) is True
    assert await check_rate_limit(key, 2, 300, is_failed_attempt=True) is True
    assert await check_rate_limit(key, 2, 300, is_failed_attempt=True) is False

    before = await _get_row(key)
    assert before is not None
    assert before.locked_until is not None

    await reset_rate_limit(key)

    after = await _get_row(key)
    assert after is not None
    assert after.attempts == 0
    assert after.locked_until == before.locked_until
    assert after.locked_until > int(time.time())


async def test_successful_login_during_lockout(rate_limit_db):
    key = _key()

    assert await check_rate_limit(key, 1, 300, is_failed_attempt=True) is True
    assert await check_rate_limit(key, 1, 300, is_failed_attempt=True) is False

    # Simulate success flow while lockout is active: keep lock, clear attempts.
    await reset_rate_limit(key)

    row = await _get_row(key)
    assert row is not None
    assert row.attempts == 0
    assert row.locked_until is not None

    # Lockout still blocks both read-only checks and failed-attempt increments.
    assert await check_rate_limit(key, 1, 300, is_failed_attempt=False) is False
    assert await check_rate_limit(key, 1, 300, is_failed_attempt=True) is False


async def test_reset_clears_attempts_not_lockout(rate_limit_db):
    key = _key()

    assert await check_rate_limit(key, 1, 300, is_failed_attempt=True) is True
    assert await check_rate_limit(key, 1, 300, is_failed_attempt=True) is False

    original = await _get_row(key)
    assert original is not None

    await reset_rate_limit(key)

    row = await _get_row(key)
    assert row is not None
    assert row.attempts == 0
    assert row.locked_until == original.locked_until


async def test_failed_attempt_increments_during_valid_window(rate_limit_db):
    key = _key()

    assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True
    assert (await _get_row(key)).attempts == 1

    assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True
    assert (await _get_row(key)).attempts == 2

    assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True
    assert (await _get_row(key)).attempts == 3


async def test_multiple_reset_cycles(rate_limit_db):
    key = _key()

    for _ in range(2):
        assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True
    await reset_rate_limit(key)

    for _ in range(3):
        assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True
    await reset_rate_limit(key)

    assert await check_rate_limit(key, 5, 300, is_failed_attempt=True) is True
    row = await _get_row(key)
    assert row is not None
    assert row.attempts == 1
