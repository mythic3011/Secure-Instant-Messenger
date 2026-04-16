"""tests/unit/test_login_rate_limit.py — login endpoint rate-limit regression tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import server.api.auth as auth
from shared.protocol import LoginRequest

pytestmark = pytest.mark.anyio


class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeDb:
    def __init__(self, user):
        self._user = user
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._user)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


async def test_login_consumes_shared_ip_quota_and_does_not_reset_on_success(monkeypatch):
    user = SimpleNamespace(id="user-1", pw_hash="stored-hash", totp_secret="encrypted-totp")
    db = _FakeDb(user)
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"))
    body = LoginRequest(username="alice", password="correct-password", totp_code="123456")

    rate_limit_calls: list[tuple[str, int, int, bool]] = []

    async def _check_rate_limit(
        key: str,
        max_attempts: int,
        window_seconds: int,
        is_failed_attempt: bool = True,
    ) -> bool:
        rate_limit_calls.append((key, max_attempts, window_seconds, is_failed_attempt))
        if not is_failed_attempt:
            return True

        attempts = sum(1 for call in rate_limit_calls if call[0] == key and call[3])
        return attempts <= 5

    monkeypatch.setattr(auth, "check_rate_limit", _check_rate_limit)
    monkeypatch.setattr(
        auth,
        "verify_password",
        lambda password, pw_hash: password == "correct-password" and pw_hash == "stored-hash",
    )
    monkeypatch.setattr(auth, "decrypt_totp_secret", lambda _user_id, _blob: "totp-secret")
    monkeypatch.setattr(
        auth,
        "verify_totp",
        lambda secret, code: secret == "totp-secret" and code == "123456",
    )
    monkeypatch.setattr(auth, "generate_token", lambda: ("raw-token", "token-hash"))
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            rate_limit_login_max=5,
            rate_limit_login_window=300,
            token_expiry_seconds=3600,
        ),
    )

    for _ in range(5):
        response = await auth.login(body=body, request=request, db=db)
        assert response.access_token == "raw-token"
        assert response.expires_at > 0

    with pytest.raises(HTTPException) as exc_info:
        await auth.login(body=body, request=request, db=db)

    assert exc_info.value.status_code == 429
    assert rate_limit_calls == [
        ("login:203.0.113.10", 5, 300, True),
        ("login:203.0.113.10", 5, 300, True),
        ("login:203.0.113.10", 5, 300, True),
        ("login:203.0.113.10", 5, 300, True),
        ("login:203.0.113.10", 5, 300, True),
        ("login:203.0.113.10", 5, 300, True),
    ]
    assert db.commits == 5
    assert len(db.added) == 5
